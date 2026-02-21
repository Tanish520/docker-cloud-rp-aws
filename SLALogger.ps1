# SLALogger.ps1
# SLA-aware logging and performance tracking
# Author: Localization Pipeline Refactoring
# Purpose: Track latency, throughput, and SLA compliance metrics

<#
.SYNOPSIS
    SLA logging module for performance tracking and compliance monitoring

.DESCRIPTION
    This module provides functions to:
    - Track localization run start/end times
    - Log per-sentence processing metrics
    - Log per-language completion metrics
    - Compute P50/P95/P99 latency percentiles
    - Detect SLA violations
    - Generate comprehensive performance reports

.NOTES
    Research Instrumentation: All metrics are logged for analysis
#>

# Global state for tracking current run
$script:CurrentRun = $null
$script:SentenceMetrics = @()
$script:LanguageMetrics = @()
$script:RunLock = New-Object System.Object

<#
.SYNOPSIS
    Initializes a new localization run

.DESCRIPTION
    Creates run tracking object and resets metrics

.PARAMETER RunId
    Unique identifier for the run (auto-generated if not provided)

.PARAMETER Config
    Configuration snapshot for the run

.OUTPUTS
    Run object with tracking metadata

.EXAMPLE
    Start-LocalizationRun -Config $config
#>
function Start-LocalizationRun {
    param(
        [Parameter(Mandatory=$false)]
        [string]$RunId = "",
        
        [Parameter(Mandatory=$false)]
        [PSCustomObject]$Config = $null
    )
    
    [System.Threading.Monitor]::Enter($script:RunLock)
    try {
        if ([string]::IsNullOrWhiteSpace($RunId)) {
            $RunId = [Guid]::NewGuid().ToString()
        }
        
        $script:CurrentRun = [PSCustomObject]@{
            RunId = $RunId
            StartTime = Get-Date
            EndTime = $null
            Duration = $null
            DurationSeconds = $null
            ConfigSnapshot = $Config
            Status = "Running"
            TotalSentences = 0
            TotalLanguages = 0
            TotalTokens = 0
            SLAViolation = $false
            Throughput = 0
        }
        
        $script:SentenceMetrics = @()
        $script:LanguageMetrics = @()
        
        Write-Verbose "Started localization run: $RunId"
        
        return $script:CurrentRun
    } finally {
        [System.Threading.Monitor]::Exit($script:RunLock)
    }
}

<#
.SYNOPSIS
    Logs metrics for a single sentence translation

.DESCRIPTION
    Records detailed metrics including latency, model used, tokens, etc.

.PARAMETER SentenceId
    Unique identifier for the sentence

.PARAMETER Text
    Original text (truncated for logging)

.PARAMETER TokenCount
    Number of tokens in the sentence

.PARAMETER ModelUsed
    Which model was used (NMT, LLM, etc.)

.PARAMETER InfrastructureUsed
    Which infrastructure was used (VM, Serverless, Default)

.PARAMETER LatencyMs
    Processing time in milliseconds

.PARAMETER SourceLanguage
    Source language code

.PARAMETER TargetLanguage
    Target language code

.EXAMPLE
    Add-SentenceMetric -SentenceId "s1" -Text "Hello" -TokenCount 2 -ModelUsed "NMT" -LatencyMs 150
#>
function Add-SentenceMetric {
    param(
        [Parameter(Mandatory=$true)]
        [string]$SentenceId,
        
        [Parameter(Mandatory=$false)]
        [string]$Text = "",
        
        [Parameter(Mandatory=$true)]
        [int]$TokenCount,
        
        [Parameter(Mandatory=$true)]
        [string]$ModelUsed,
        
        [Parameter(Mandatory=$false)]
        [string]$InfrastructureUsed = "Default",
        
        [Parameter(Mandatory=$true)]
        [double]$LatencyMs,
        
        [Parameter(Mandatory=$false)]
        [string]$SourceLanguage = "",
        
        [Parameter(Mandatory=$false)]
        [string]$TargetLanguage = ""
    )
    
    [System.Threading.Monitor]::Enter($script:RunLock)
    try {
        $metric = [PSCustomObject]@{
            SentenceId = $SentenceId
            TextPreview = if ($Text.Length -gt 50) { $Text.Substring(0, 50) + "..." } else { $Text }
            TextLength = $Text.Length
            TokenCount = $TokenCount
            ModelUsed = $ModelUsed
            InfrastructureUsed = $InfrastructureUsed
            LatencyMs = $LatencyMs
            SourceLanguage = $SourceLanguage
            TargetLanguage = $TargetLanguage
            Timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffZ"
        }
        
        $script:SentenceMetrics += $metric
        $script:CurrentRun.TotalSentences++
        $script:CurrentRun.TotalTokens += $TokenCount
        
        Write-Verbose "Logged sentence metric: $SentenceId, $TokenCount tokens, $LatencyMs ms, $ModelUsed"
        
        return $metric
    } finally {
        [System.Threading.Monitor]::Exit($script:RunLock)
    }
}

<#
.SYNOPSIS
    Logs metrics for a language completion

.DESCRIPTION
    Records when a language's translations are complete

.PARAMETER LanguageCode
    Target language code

.PARAMETER SentenceCount
    Number of sentences translated

.PARAMETER TotalTokens
    Total tokens processed for this language

.PARAMETER CompletionTimeMs
    Total time to complete this language

.EXAMPLE
    Add-LanguageMetric -LanguageCode "fr-FR" -SentenceCount 100 -TotalTokens 5000 -CompletionTimeMs 30000
#>
function Add-LanguageMetric {
    param(
        [Parameter(Mandatory=$true)]
        [string]$LanguageCode,
        
        [Parameter(Mandatory=$true)]
        [int]$SentenceCount,
        
        [Parameter(Mandatory=$true)]
        [int]$TotalTokens,
        
        [Parameter(Mandatory=$true)]
        [double]$CompletionTimeMs
    )
    
    [System.Threading.Monitor]::Enter($script:RunLock)
    try {
        $metric = [PSCustomObject]@{
            LanguageCode = $LanguageCode
            SentenceCount = $SentenceCount
            TotalTokens = $TotalTokens
            CompletionTimeMs = $CompletionTimeMs
            AvgLatencyPerSentence = [Math]::Round($CompletionTimeMs / $SentenceCount, 2)
            Throughput = [Math]::Round($SentenceCount / ($CompletionTimeMs / 1000.0), 2)  # sentences/sec
            Timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffZ"
        }
        
        $script:LanguageMetrics += $metric
        $script:CurrentRun.TotalLanguages++
        
        Write-Verbose "Logged language metric: $LanguageCode, $SentenceCount sentences, $CompletionTimeMs ms"
        
        return $metric
    } finally {
        [System.Threading.Monitor]::Exit($script:RunLock)
    }
}

<#
.SYNOPSIS
    Computes latency percentiles

.DESCRIPTION
    Calculates P50, P95, P99 latency from sentence metrics

.PARAMETER Latencies
    Array of latency values in milliseconds

.OUTPUTS
    PSCustomObject with percentile values

.EXAMPLE
    Get-LatencyPercentiles -Latencies $latencyArray
#>
function Get-LatencyPercentiles {
    param(
        [Parameter(Mandatory=$true)]
        [double[]]$Latencies
    )
    
    if ($Latencies.Count -eq 0) {
        return [PSCustomObject]@{
            P50 = 0
            P95 = 0
            P99 = 0
            Min = 0
            Max = 0
            Mean = 0
        }
    }
    
    $sorted = $Latencies | Sort-Object
    $count = $sorted.Count
    
    $p50Index = [Math]::Floor($count * 0.50)
    $p95Index = [Math]::Floor($count * 0.95)
    $p99Index = [Math]::Floor($count * 0.99)
    
    $percentiles = [PSCustomObject]@{
        P50 = [Math]::Round($sorted[$p50Index], 2)
        P95 = [Math]::Round($sorted[$p95Index], 2)
        P99 = [Math]::Round($sorted[$p99Index], 2)
        Min = [Math]::Round(($sorted | Measure-Object -Minimum).Minimum, 2)
        Max = [Math]::Round(($sorted | Measure-Object -Maximum).Maximum, 2)
        Mean = [Math]::Round(($sorted | Measure-Object -Average).Average, 2)
    }
    
    return $percentiles
}

<#
.SYNOPSIS
    Completes the current localization run

.DESCRIPTION
    Finalizes run, computes aggregate metrics, detects SLA violations

.PARAMETER SLADeadlineSeconds
    SLA deadline in seconds (default: 3600)

.OUTPUTS
    Completed run object with all metrics

.EXAMPLE
    Complete-LocalizationRun -SLADeadlineSeconds 3600
#>
function Complete-LocalizationRun {
    param(
        [Parameter(Mandatory=$false)]
        [int]$SLADeadlineSeconds = 3600
    )
    
    [System.Threading.Monitor]::Enter($script:RunLock)
    try {
        if ($null -eq $script:CurrentRun) {
            Write-Warning "No active run to complete"
            return $null
        }
        
        $script:CurrentRun.EndTime = Get-Date
        $script:CurrentRun.Duration = ($script:CurrentRun.EndTime - $script:CurrentRun.StartTime).TotalMilliseconds
        $script:CurrentRun.DurationSeconds = [Math]::Round($script:CurrentRun.Duration / 1000.0, 2)
        
        # Compute latency percentiles
        $latencies = @($script:SentenceMetrics | ForEach-Object { $_.LatencyMs })
        if ($latencies.Count -gt 0) {
            $script:CurrentRun.LatencyPercentiles = Get-LatencyPercentiles -Latencies $latencies
        } else {
            # No latency data available (e.g., Function1 which doesn't do translation)
            $script:CurrentRun.LatencyPercentiles = $null
        }
        
        # Detect SLA violations
        $script:CurrentRun.SLADeadlineSeconds = $SLADeadlineSeconds
        $script:CurrentRun.SLAViolation = $script:CurrentRun.DurationSeconds -gt $SLADeadlineSeconds
        
        # Compute throughput
        $script:CurrentRun.Throughput = if ($script:CurrentRun.DurationSeconds -gt 0) {
            [Math]::Round($script:CurrentRun.TotalSentences / $script:CurrentRun.DurationSeconds, 2)
        } else { 0 }
        
        # Model usage statistics
        $nmtCount = ($script:SentenceMetrics | Where-Object { $_.ModelUsed -like "NMT*" }).Count
        $llmCount = ($script:SentenceMetrics | Where-Object { $_.ModelUsed -eq "LLM" }).Count
        
        $script:CurrentRun.ModelUsageStats = [PSCustomObject]@{
            NMTCount = $nmtCount
            LLMCount = $llmCount
            NMTPercentage = if ($script:CurrentRun.TotalSentences -gt 0) {
                [Math]::Round(($nmtCount / $script:CurrentRun.TotalSentences) * 100, 2)
            } else { 0 }
            LLMPercentage = if ($script:CurrentRun.TotalSentences -gt 0) {
                [Math]::Round(($llmCount / $script:CurrentRun.TotalSentences) * 100, 2)
            } else { 0 }
        }
        
        # Infrastructure usage statistics
        $vmCount = ($script:SentenceMetrics | Where-Object { $_.InfrastructureUsed -eq "VM" }).Count
        $serverlessCount = ($script:SentenceMetrics | Where-Object { $_.InfrastructureUsed -eq "Serverless" }).Count
        $defaultCount = ($script:SentenceMetrics | Where-Object { $_.InfrastructureUsed -eq "Default" }).Count
        
        $script:CurrentRun.InfraUsageStats = [PSCustomObject]@{
            VMCount = $vmCount
            ServerlessCount = $serverlessCount
            DefaultCount = $defaultCount
        }
        
        $script:CurrentRun.Status = if ($script:CurrentRun.SLAViolation) { "Completed (SLA Violation)" } else { "Completed" }
        
        Write-Verbose "Completed localization run: $($script:CurrentRun.RunId), $($script:CurrentRun.DurationSeconds)s, SLA: $($script:CurrentRun.SLAViolation)"
        
        return $script:CurrentRun
    } finally {
        [System.Threading.Monitor]::Exit($script:RunLock)
    }
}

<#
.SYNOPSIS
    Exports SLA report to JSON file

.DESCRIPTION
    Saves comprehensive metrics to file for analysis

.PARAMETER OutputPath
    Path to output JSON file

.PARAMETER IncludeSentenceMetrics
    Whether to include detailed per-sentence metrics (default: true)

.PARAMETER IncludeLanguageMetrics
    Whether to include per-language metrics (default: true)

.EXAMPLE
    Export-SLAReport -OutputPath "logs/sla-report.json"
#>
function Export-SLAReport {
    param(
        [Parameter(Mandatory=$true)]
        [string]$OutputPath,
        
        [Parameter(Mandatory=$false)]
        [bool]$IncludeSentenceMetrics = $true,
        
        [Parameter(Mandatory=$false)]
        [bool]$IncludeLanguageMetrics = $true
    )
    
    [System.Threading.Monitor]::Enter($script:RunLock)
    try {
        if ($null -eq $script:CurrentRun) {
            Write-Warning "No run data to export"
            return $false
        }
        
        $report = @{
            Run = $script:CurrentRun
        }
        
        if ($IncludeSentenceMetrics) {
            $report.SentenceMetrics = $script:SentenceMetrics
        }
        
        if ($IncludeLanguageMetrics) {
            $report.LanguageMetrics = $script:LanguageMetrics
        }
        
        # Ensure output directory exists
        $outputDir = Split-Path -Path $OutputPath -Parent
        if (![string]::IsNullOrWhiteSpace($outputDir) -and !(Test-Path -Path $outputDir)) {
            New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
        }
        
        $report | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding UTF8
        
        Write-Host "SLA report exported to: $OutputPath"
        Write-Host "Run ID: $($script:CurrentRun.RunId)"
        Write-Host "Duration: $($script:CurrentRun.DurationSeconds)s"
        Write-Host "Total Sentences: $($script:CurrentRun.TotalSentences)"
        Write-Host "Total Tokens: $($script:CurrentRun.TotalTokens)"
        Write-Host "Throughput: $($script:CurrentRun.Throughput) sentences/sec"
        Write-Host "P50 Latency: $($script:CurrentRun.LatencyPercentiles.P50) ms"
        Write-Host "P95 Latency: $($script:CurrentRun.LatencyPercentiles.P95) ms"
        Write-Host "P99 Latency: $($script:CurrentRun.LatencyPercentiles.P99) ms"
        Write-Host "SLA Violation: $($script:CurrentRun.SLAViolation)"
        
        return $true
    } catch {
        Write-Error "Failed to export SLA report: $_"
        return $false
    } finally {
        [System.Threading.Monitor]::Exit($script:RunLock)
    }
}

<#
.SYNOPSIS
    Gets current run status

.DESCRIPTION
    Returns current run object without modifying it

.OUTPUTS
    Current run object

.EXAMPLE
    Get-CurrentRunStatus
#>
function Get-CurrentRunStatus {
    [System.Threading.Monitor]::Enter($script:RunLock)
    try {
        return $script:CurrentRun
    } finally {
        [System.Threading.Monitor]::Exit($script:RunLock)
    }
}
