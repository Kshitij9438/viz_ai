Write-Host "=== TIER 1 SYSTEM TEST START ==="

$base = "http://localhost:8000"

# -------------------------------
# TEST 1 — Redis fallback
# -------------------------------
Write-Host "`n[TEST 1] Redis fallback"

try {
    $response = Invoke-RestMethod -Uri "$base/api/v1/chat" -Method POST `
        -ContentType "application/json" `
        -Body '{"message":"generate a futuristic cyberpunk city","session_id":null}'
}
catch {
    Write-Host "FAIL: Chat request failed"
    Write-Host $_
    exit
}

if ($response.job_id) {
    Write-Host "PASS: Job created"
} else {
    Write-Host "FAIL: Job not created"
}

# -------------------------------
# TEST 2 — Poll job
# -------------------------------
Write-Host "`n[TEST 2] Poll job"

$jobId = $response.job_id
$done = $false

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    $job = Invoke-RestMethod -Uri "$base/api/v1/jobs/$jobId"

    if ($job.status -eq "done") {
        Write-Host "PASS: Job completed"
        $done = $true
        break
    }
}

if (-not $done) {
    Write-Host "FAIL: Job did not complete"
}

# -------------------------------
# TEST 3 — Generation gate
# -------------------------------
Write-Host "`n[TEST 3] Generation gate"

$response2 = Invoke-RestMethod -Uri "$base/api/v1/chat" -Method POST `
    -ContentType "application/json" `
    -Body '{"message":"paint something that feels like my last year emotionally","session_id":null}'

if ($response2.job_id) {
    Write-Host "PASS: Gate allowed generation"
} else {
    Write-Host "FAIL: Gate blocked generation"
}

# -------------------------------
# TEST 4 — Static file check
# -------------------------------
Write-Host "`n[TEST 4] Storage URL"

if ($done -and $job.result.asset_bundle.assets[0].url) {
    $imgUrl = $job.result.asset_bundle.assets[0].url
    Write-Host "Checking image URL: $imgUrl"

    try {
        Invoke-WebRequest -Uri $imgUrl -OutFile "test_image.jpg"
        Write-Host "PASS: Image accessible"
    } catch {
        Write-Host "FAIL: Image not accessible"
    }
} else {
    Write-Host "SKIP: No image to test"
}

Write-Host "`n=== TEST COMPLETE ==="
