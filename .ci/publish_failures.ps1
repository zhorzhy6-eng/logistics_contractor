# Печатает отказы слоя тестов так, чтобы их было видно БЕЗ входа в GitHub.
#
# Зачем: логи GitHub Actions отдаёт только владельцу репозитория
# (`/actions/jobs/<id>/logs` → 403 без входа), а аннотации check-run
# читаются публично:
#     https://api.github.com/repos/<owner>/<repo>/check-runs/<job_id>/annotations
# Поэтому итоговая строка pytest, имена упавших тестов, первые строки ошибок,
# размер junit-отчёта и ХВОСТ вывода печатаются workflow-командами:
#   * `::error::`  — только когда отказы ЕСТЬ;
#   * `::notice::` — информационные (итог, хвост, размер отчёта), чтобы
#     зелёный прогон не выглядел красным в списке аннотаций.
#
# Хвост вывода — не роскошь: если прогон оборвался НАТИВНО (0xC0000409),
# junit пустой и списка отказов нет вообще, и единственный след — последние
# строки вывода (поэтому UI-слой в CI идёт с `-v`: в строках видно имя теста).
#
# Скрипт НИКОГДА не роняет работу: он про диагностику, а не про проверку.
# Вызывается из workflow так (dot-source, чтобы не плодить процессы):
#     . ./.ci/publish_failures.ps1 -Layer unit

param(
    [Parameter(Mandatory = $true)][string]$Layer,
    [int]$MaxAnnotations = 25
)

$ErrorActionPreference = "Continue"

function Get-Escaped([string]$text) {
    if ($null -eq $text) { return "" }
    # % \r \n — служебные символы workflow-команд, их надо экранировать.
    return $text.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
}

function Write-Annotation([string]$title, [string]$message,
                          [string]$Level = "notice") {
    Write-Output ("::{0} title={1}::{2}" -f $Level, (Get-Escaped $title),
                  (Get-Escaped $message))
}

$junitPath = "tests/_tmp/junit-$Layer.xml"
$outputPath = "tests/_tmp/$Layer-output.txt"

# ── 1. Вывод pytest: итоговая строка, отказы, хвост ──
$tail = @()
if (Test-Path $outputPath) {
    $tail = @(Get-Content $outputPath -Encoding utf8 | Where-Object { $_.Trim() -ne "" })
} else {
    Write-Output "Вывод pytest не сохранён: $outputPath"
}

$summary = if ($tail.Count -gt 0) { [string]$tail[-1] } else { "" }

# `-rf` печатает по строке на отказ: в ней есть и имя теста, и текст ошибки.
# Junit иногда отдаёт пустое сообщение — тогда причина видна ТОЛЬКО здесь.
$failedLines = @($tail | Where-Object { $_ -match '^(FAILED|ERROR)\s' } |
                 Select-Object -First $MaxAnnotations)

# ── 2. Junit: отказы поимённо и размер отчёта ──
$cases = @()
$failures = @()
if (Test-Path $junitPath) {
    try {
        [xml]$report = Get-Content $junitPath -Encoding utf8
        $cases = @($report.testsuites.testsuite | ForEach-Object { $_.testcase })
        $failures = @($cases | Where-Object { $_.failure -or $_.error })
    } catch {
        Write-Output "junit-отчёт не разобран: $($_.Exception.Message)"
    }
} else {
    Write-Output "junit-отчёта нет: $junitPath"
}

$hasFailures = ($failures.Count -gt 0)
$level = if ($hasFailures) { "error" } else { "notice" }

# ── 3. Аннотации ──
if ($summary) {
    Write-Output "ИТОГ pytest (${Layer}): $summary"
    Write-Annotation "${Layer}: итог pytest" $summary $level
}

if ($failedLines.Count -gt 0) {
    Write-Annotation "${Layer}: отказы (из вывода pytest)" ($failedLines -join "`n") "error"
}

foreach ($case in ($failures | Select-Object -First $MaxAnnotations)) {
    $detail = ""
    if ($case.failure) { $detail = [string]$case.failure.message }
    elseif ($case.error) { $detail = [string]$case.error.message }

    # В сообщении junit лежит всё: и суть отказа, и вывод подпроцесса-пробы,
    # поэтому в аннотацию идут первые строки, а не одна.
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
    if ($messageLines.Count -eq 0) { $messageLines = @("(сообщение и traceback пусты)") }

    $name = "$($case.classname)::$($case.name)"
    Write-Output "  FAIL: $name"
    foreach ($line in $messageLines) { Write-Output "        $line" }

    Write-Annotation "FAIL $Layer" ("$name`n" + ($messageLines -join "`n")) "error"
}

# Число тестов в отчёте — признак обрыва прогона: если оно сильно меньше
# ожидаемого, процесс упал нативно и отчёт частичный.
Write-Output "тестов в junit-отчёте: $($cases.Count); отказов: $($failures.Count)"
Write-Annotation "${Layer}: размер отчёта" `
    "тестов в отчёте $($cases.Count), отказов $($failures.Count)" $level

if ($cases.Count -gt 0) {
    $lastCase = $cases[-1]
    $lastName = "$($lastCase.classname)::$($lastCase.name)"
    Write-Output "последний тест в отчёте: $lastName"
    Write-Annotation "${Layer}: последний тест в отчёте" $lastName $level
}

if ($tail.Count -gt 0) {
    $tailLines = @($tail | Select-Object -Last 20)
    Write-Output "── последние строки вывода ──"
    $tailLines | ForEach-Object { Write-Output $_ }
    Write-Annotation "${Layer}: хвост вывода" ($tailLines -join "`n") $level
}

# ── 4. Сводка шага (видна на странице прогона) ──
if ($env:GITHUB_STEP_SUMMARY) {
    $body = @("## Слой $Layer", "")
    if ($summary) { $body += @('```', $summary, '```', "") }
    $body += "Тестов в отчёте: $($cases.Count); отказов: $($failures.Count)"
    if ($failures.Count -gt 0) {
        $body += ""
        $body += "| Тест | Первая строка ошибки |"
        $body += "|---|---|"
        foreach ($case in ($failures | Select-Object -First $MaxAnnotations)) {
            $detail = ""
            if ($case.failure) { $detail = [string]$case.failure.message }
            elseif ($case.error) { $detail = [string]$case.error.message }
            $first = (([string]$detail -split "`n")[0] -replace '\|', '\|')
            $body += "| ``$($case.classname)::$($case.name)`` | $first |"
        }
    }
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($body -join "`n") -Encoding utf8
}

# Диагностика не должна ронять работу.
exit 0
