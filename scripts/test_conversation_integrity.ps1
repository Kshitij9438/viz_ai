$ErrorActionPreference = "Stop"

Write-Host "Starting test_conversation_integrity.ps1" -ForegroundColor Cyan

# Ensure server is running (assumes it is running locally on port 8000 or starts it)
$ApiUrl = "http://localhost:8000"

# Create a random session ID
$SessionId = [guid]::NewGuid().ToString()

Function Send-Chat {
    param([string]$Message, [string]$EventName = "user_message")
    $body = @{
        session_id = $SessionId
        message = $Message
        event_name = $EventName
    } | ConvertTo-Json

    $response = Invoke-RestMethod -Uri "$ApiUrl/api/v1/chat" -Method Post -Body $body -Headers @{"Content-Type"="application/json"; "X-Guest-Token"="test-guest-token"}
    return $response
}

Write-Host "1. Testing session load (system event)..."
$res1 = Send-Chat -Message "load" -EventName "session_load"
if ($res1.job_id) {
    Write-Error "Session load triggered generation!"
    exit 1
}
Write-Host "   OK: No job created." -ForegroundColor Green

Write-Host "2. Testing pure chat (no generation intent)..."
$res2 = Send-Chat -Message "Hello, I want to chat about dogs."
if ($res2.job_id) {
    Write-Error "Pure chat triggered generation!"
    exit 1
}
Write-Host "   OK: No job created." -ForegroundColor Green

Write-Host "3. Testing descriptive / refinement chat..."
$res3 = Send-Chat -Message "Create a cinematic, ultra detailed landscape of a mountain with a river running through it."
if ($res3.job_id) {
    Write-Error "Refinement/Descriptive chat triggered generation without confirmation!"
    exit 1
}
Write-Host "   OK: No job created. Prompted for confirmation." -ForegroundColor Green

Write-Host "4. Testing explicit confirmation..."
$res4 = Send-Chat -Message "yes, go ahead and generate it."
if (-not $res4.job_id) {
    Write-Error "Confirmation did NOT trigger generation!"
    exit 1
}
Write-Host "   OK: Job created after confirmation. Job ID: $($res4.job_id)" -ForegroundColor Green

Write-Host "All tests passed successfully!" -ForegroundColor Green
exit 0

