$base = "http://127.0.0.1:8000/api/v1"

Write-Host "=== FULL SYSTEM TEST START ==="

# ---------------------------------------------------------
# TEST 1 - CHAT REQUEST
# ---------------------------------------------------------
try {
    $response = Invoke-RestMethod -Uri "$base/chat" -Method POST `
        -ContentType "application/json" `
        -Body '{"message":"generate a futuristic city skyline","session_id":null}'

    if (-not $response.job_id) {
        Write-Host "FAIL: No job_id returned"
        exit
    }

    Write-Host "PASS: Chat created job"
    $jobId = $response.job_id
}
catch {
    Write-Host "FAIL: Chat request failed"
    Write-Host $_
    exit
}

# ---------------------------------------------------------
# TEST 2 - POLLING LOOP
# ---------------------------------------------------------
$maxTries = 30
$completed = $false

for ($i = 0; $i -lt $maxTries; $i++) {
    Start-Sleep -Seconds 2

    try {
        $job = Invoke-RestMethod -Uri "$base/jobs/$jobId"

        if ($job.status -eq "done") {
            Write-Host "PASS: Job completed"
            $completed = $true
            break
        }

        if ($job.status -eq "failed") {
            Write-Host "FAIL: Job failed"
            exit
        }
    }
    catch {
        Write-Host "WARN: Poll error"
    }
}

if (-not $completed) {
    Write-Host "FAIL: Job did not complete in time"
    exit
}

# ---------------------------------------------------------
# TEST 3 - RESULT VALIDATION
# ---------------------------------------------------------
if (-not $job.result) {
    Write-Host "FAIL: No result payload"
    exit
}

Write-Host "PASS: Result exists"

# ---------------------------------------------------------
# TEST 4 - IMAGE URL CHECK
# ---------------------------------------------------------
$assets = $job.result.asset_bundle.assets

if (-not $assets -or $assets.Count -eq 0) {
    Write-Host "FAIL: No assets found"
    exit
}

$url = $assets[0].url
Write-Host "Checking image URL: $url"

try {
    $img = Invoke-WebRequest -Uri $url
    if ($img.StatusCode -eq 200) {
        Write-Host "PASS: Image accessible"
    }
    else {
        Write-Host "FAIL: Image not accessible"
        exit
    }
}
catch {
    Write-Host "FAIL: Image fetch failed"
    exit
}

# ---------------------------------------------------------
# TEST 5 - NO RETRY AFTER SUCCESS
# ---------------------------------------------------------
Write-Host "Check logs manually for:"
Write-Host "- job_retry_scheduled AFTER job_succeeded (should NOT exist)"

Write-Host "=== FULL SYSTEM TEST COMPLETE ==="
