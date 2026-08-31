$ErrorActionPreference = "Stop"

Write-Host "Starting test_suggestion_flow.ps1" -ForegroundColor Cyan

$ApiUrl = "http://localhost:8000"
$SessionId = [guid]::NewGuid().ToString()

Function Send-Chat {
    param([string]$Message, [string]$EventName = "user_message")
    $body = @{
        session_id = $SessionId
        message = $Message
        event_name = $EventName
    } | ConvertTo-Json

    $response = Invoke-RestMethod -Uri "$ApiUrl/api/v1/chat" -Method Post -Body $body -Headers @{"Content-Type"="application/json"; "X-Guest-Token"="test"} -SkipHttpErrorCheck
    return $response
}

Write-Host "1. Testing explore intent..."
$res1 = Send-Chat -Message "give me some ideas for a poster"
if ($res1.job_id) {
    Write-Error "Explore triggered generation!"
    exit 1
}
if ($res1.reply -notmatch "1\..*2\.") {
    Write-Error "Response did not contain numbered options!`nResponse: $($res1.reply)"
    exit 1
}
Write-Host "   OK: No job created. Received options:`n$($res1.reply)" -ForegroundColor Green

Write-Host "`n2. Testing option selection..."
$res2 = Send-Chat -Message "2"
if ($res2.job_id) {
    Write-Error "Selection triggered generation!"
    exit 1
}
if ($res2.reply -notmatch "Nice choice") {
    Write-Error "Selection did not trigger the 'Nice choice' response!`nResponse: $($res2.reply)"
    exit 1
}
Write-Host "   OK: Context updated and user prompted to refine or generate." -ForegroundColor Green

Write-Host "`n3. Testing confirmation..."
$res3 = Send-Chat -Message "yes"
if (-not $res3.job_id) {
    Write-Error "Confirmation did NOT trigger generation!"
    exit 1
}
Write-Host "   OK: Job created after confirmation. Job ID: $($res3.job_id)" -ForegroundColor Green

Write-Host "`nAll tests passed successfully!" -ForegroundColor Green
exit 0
