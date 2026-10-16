# Печатает отказы слоя тестов так, чтобы их было видно БЕЗ входа в GitHub.
#
# Зачем: логи GitHub Actions отдаёт только владельцу репозитория
# (`/actions/jobs/<id>/logs` → 403 без входа), а аннотации check-run
# читаются публично:
#     https://api.github.com/repos/<owner>/<repo>/check-runs/<job_id>/annotations
# Поэтому имена упавших тестов и итоговая строка pytest печатаются
# workflow-командами `::error::` — GitHub превращает их в аннотации.
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

function Write-Annotation([string]$title, [string]$message) {
    Write-Output ("::error title={0}::{1}" -f (Get-Escaped $title), (Get-Escaped $message))
}

$junitPath = "tests/_tmp/junit-$Layer.xml"
$outputPath = "tests/_tmp/$Layer-output.txt"

# ── 1. Итоговая строка pytest и хвост вывода ──
if (Test-Path $outputPath) {
    $tail = @(Get-Content $outputPath -Encoding utf8 | Where-Object { $_.Trim() -ne "" })
    if ($tail.Count -gt 0) {
        $last = [string]$tail[-1]
        Write-Output "ИТОГ pytest (${Layer}): $last"
        Write-Annotation "${Layer}: итог pytest" $last
    }
    Write-Output "── последние строки вывода ──"
    $tail | Select-Object -Last 15 | ForEach-Object { Write-Output $_ }
} else {
    Write-Output "Вывод pytest не сохранён: $outputPath"
    Write-Annotation "${Layer}: вывод pytest" "файл $outputPath не найден"
}

# ── 2. Отказы поимённо и размер отчёта ──
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

# Число тестов в отчёте — признак обрыва прогона: если оно сильно меньше
# ожидаемого, процесс упал нативно (0xC0000409), и отчёт частичный.
Write-Output "тестов в junit-отчёте: $($cases.Count); отказов: $($failures.Count)"
Write-Annotation "${Layer}: размер отчёта" "тестов в отчёте $($cases.Count), отказов $($failures.Count)"

# Последний тест в отчёте — то место, где прогон оборвался, если он оборвался.
if ($cases.Count -gt 0) {
    $lastCase = $cases[-1]
    $lastName = "$($lastCase.classname)::$($lastCase.name)"
    Write-Output "последний тест в отчёте: $lastName"
    Write-Annotation "${Layer}: последний тест в отчёте" $lastName
}

foreach ($case in ($failures | Select-Object -First $MaxAnnotations)) {
    $detail = ""
    if ($case.failure) { $detail = [string]$case.failure.message }
    elseif ($case.error) { $detail = [string]$case.error.message }

    # В сообщении junit лежит всё: и суть отказа, и вывод подпроцесса-пробы,
    # поэтому в аннотацию идут первые строки, а не одна. Иначе по аннотации
    # видно только «проба упала», а почему — нет.
    $messageLines = @(
        [string]$detail -split "`r?`n" |
        Where-Object { $_.Trim() -ne "" } |
        Select-Object -First 12
    )
    if ($messageLines.Count -eq 0) { $messageLines = @("(сообщение пустое)") }

    $name = "$($case.classname)::$($case.name)"
    Write-Output "  FAIL: $name"
    foreach ($line in $messageLines) { Write-Output "        $line" }

    Write-Annotation "FAIL $Layer" ("$name`n" + ($messageLines -join "`n"))
}

# ── 3. Сводка шага (видна на странице прогона) ──
if ($env:GITHUB_STEP_SUMMARY) {
    $body = @("## Слой $Layer", "")
    if ($tail.Count -gt 0) {
        $body += @('```', [string]$tail[-1], '```', "")
    }
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
