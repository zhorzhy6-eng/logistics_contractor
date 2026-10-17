# Печатает отказы тестов так, чтобы их было видно БЕЗ входа в GitHub.
#
# Зачем: логи GitHub Actions отдаёт только владельцу репозитория
# (`/actions/jobs/<id>/logs` → 403 без входа), а аннотации check-run
# читаются публично:
#     https://api.github.com/repos/<owner>/<repo>/check-runs/<job_id>/annotations
# Поэтому итоговые строки pytest, имена упавших тестов, первые строки ошибок,
# размер junit-отчёта и ХВОСТ вывода печатаются workflow-командами:
#   * `::error::`  — по одной на ЧАСТЬ, у которой есть отказы или обрыв;
#   * `::notice::` — одна общая сводка по всем частям.
#
# ПОЧЕМУ ПО ОДНОЙ НА ЧАСТЬ, А НЕ ПО АННОТАЦИИ НА КАЖДЫЙ ФАКТ: GitHub
# принимает не больше 10 аннотаций одного уровня на ШАГ, остальные молча
# отбрасывает (на этом уже обожглись в прогоне №18: часть `main_window_b`
# осталась без аннотаций). Частей шесть — значит на часть одна аннотация,
# плюс одна общая сводка: ровно семь.
#
# Хвост вывода — не роскошь: если прогон оборвался НАТИВНО (0xC0000409),
# junit пустой и списка отказов нет вообще, и единственный след — последние
# строки вывода (поэтому части UI идут с `-v`: в строках видно имя теста).
#
# Скрипт НИКОГДА не роняет работу: он про диагностику, а не про проверку.
# Вызывается из workflow так (dot-source, чтобы не плодить процессы):
#     . ./.ci/publish_failures.ps1 -Parts unit
#     . ./.ci/publish_failures.ps1 -Parts ui-tabs,ui-windows

param(
    [Parameter(Mandatory = $true)][string[]]$Parts,
    [int]$MaxFailedLines = 25,
    [int]$MaxMessageLength = 8000
)

$ErrorActionPreference = "Continue"

function Get-Escaped([string]$text) {
    if ($null -eq $text) { return "" }
    # % \r \n — служебные символы workflow-команд, их надо экранировать.
    return $text.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
}

function Write-Annotation([string]$title, [string]$message,
                          [string]$Level = "notice") {
    if ($message.Length -gt $MaxMessageLength) {
        $message = $message.Substring(0, $MaxMessageLength) + "`n… (обрезано)"
    }
    Write-Output ("::{0} title={1}::{2}" -f $Level, (Get-Escaped $title),
                  (Get-Escaped $message))
}

$summaryRows = @()

foreach ($part in $Parts) {
    $junitPath = "tests/_tmp/junit-$part.xml"
    $outputPath = "tests/_tmp/$part-output.txt"

    # ── Вывод pytest: итоговая строка, отказы, хвост ──
    $tail = @()
    if (Test-Path $outputPath) {
        $tail = @(Get-Content $outputPath -Encoding utf8 |
                  Where-Object { $_.Trim() -ne "" })
    } else {
        Write-Output "Вывод pytest не сохранён: $outputPath"
    }
    $summary = if ($tail.Count -gt 0) { [string]$tail[-1] } else { "" }

    # `-rf` печатает по строке на отказ: в ней есть и имя теста, и текст
    # ошибки. Junit иногда отдаёт пустое сообщение — тогда причина видна
    # ТОЛЬКО здесь.
    $failedLines = @($tail | Where-Object { $_ -match '^(FAILED|ERROR)\s' } |
                     Select-Object -First $MaxFailedLines)

    # ── Junit: отказы поимённо и размер отчёта ──
    $cases = @()
    $failures = @()
    if (Test-Path $junitPath) {
        try {
            [xml]$report = Get-Content $junitPath -Encoding utf8
            $cases = @($report.testsuites.testsuite | ForEach-Object { $_.testcase })
            $failures = @($cases | Where-Object { $_.failure -or $_.error })
        } catch {
            Write-Output "junit-отчёт не разобран ($part): $($_.Exception.Message)"
        }
    } else {
        Write-Output "junit-отчёта нет: $junitPath"
    }

    $sizeNote = "тестов в отчёте $($cases.Count), отказов $($failures.Count)"
    Write-Output "=== $part ==="
    Write-Output "ИТОГ pytest: $summary"
    Write-Output $sizeNote

    $summaryRows += "- **$part**: $summary | $sizeNote"

    # Признак нативного обрыва: тесты шли, а отчёт пуст.
    $crashed = ($cases.Count -eq 0)
    $hasFailures = ($crashed -or $failures.Count -gt 0 -or $failedLines.Count -gt 0)

    $body = @("$part", "итог: $summary", $sizeNote, "")

    if ($crashed) {
        $body += "ПОХОЖЕ НА НАТИВНЫЙ ОБРЫВ: junit пуст ($sizeNote)."
        $body += "Последние строки вывода называют тест, на котором процесс умер."
        $body += ""
    }

    if ($failedLines.Count -gt 0) {
        $body += "Отказы (из вывода pytest):"
        $body += $failedLines
        $body += ""
    }

    foreach ($case in ($failures | Select-Object -First $MaxFailedLines)) {
        $detail = ""
        if ($case.failure) { $detail = [string]$case.failure.message }
        elseif ($case.error) { $detail = [string]$case.error.message }

        $messageLines = @(
            [string]$detail -split "`r?`n" |
            Where-Object { $_.Trim() -ne "" } |
            Select-Object -First 12
        )
        if ($messageLines.Count -eq 0) {
            # Сообщения нет — берём ВНУТРЕННИЙ текст элемента: там traceback.
            $inner = ""
            if ($case.failure) { $inner = [string]$case.failure.'#text' }
            elseif ($case.error) { $inner = [string]$case.error.'#text' }
            $messageLines = @(
                $inner -split "`r?`n" |
                Where-Object { $_.Trim() -ne "" } |
                Select-Object -First 12
            )
        }
        if ($messageLines.Count -eq 0) { $messageLines = @("(сообщение пусто)") }

        $body += "FAIL $($case.classname)::$($case.name)"
        $body += $messageLines
        $body += ""
    }

    if ($tail.Count -gt 0) {
        $body += "Хвост вывода (последние 20 строк):"
        $body += @($tail | Select-Object -Last 20)
    }

    if ($hasFailures) {
        Write-Annotation "$part" ($body -join "`n") "error"
    } else {
        Write-Annotation "$part" ($body -join "`n") "notice"
    }

    if ($env:GITHUB_STEP_SUMMARY) {
        $block = [string]::Join("`n", @("## $part", "", '```', $summary, '```',
                                        $sizeNote, ""))
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $block
    }
}

# ── Общая сводка по частям (одна аннотация на шаг) ──
Write-Annotation "итог по частям" ($summaryRows -join "`n")
