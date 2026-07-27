$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repositoryRoot "backend\docker-compose.vision.yml"
$gpuComposeFile = Join-Path $repositoryRoot "backend\docker-compose.vision.gpu.yml"

$gpuAvailable = $false
docker run --rm --gpus all alpine:3.20 sh -c "test -e /dev/dxg || test -e /dev/nvidia0"
if ($LASTEXITCODE -eq 0) {
    $gpuAvailable = $true
}

if ($gpuAvailable) {
    Write-Host "Docker GPU detected. Starting YOLO26m with GPU priority."
    docker compose -f $composeFile -f $gpuComposeFile build vision
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the GPU-enabled YOLO vision service image."
    }
    docker compose -f $composeFile -f $gpuComposeFile up -d vision
} else {
    Write-Host "Docker GPU is unavailable. Starting YOLO26m on CPU."
    docker compose -f $composeFile build vision
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the CPU-only YOLO vision service image."
    }
    docker compose -f $composeFile up -d vision
}

if ($LASTEXITCODE -ne 0) {
    throw "Failed to start the YOLO vision service."
}

docker compose -f $composeFile ps vision
