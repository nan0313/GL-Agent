param([string]$OutputPath, [switch]$SelfTest)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "portable_common.ps1")

function Write-VerificationResult {
    param([hashtable]$Result)
    $Json = $Result | ConvertTo-Json -Depth 12
    if ($OutputPath) {
        $Parent = Split-Path -Parent $OutputPath
        if ($Parent) { New-Item -ItemType Directory -Force -Path $Parent | Out-Null }
        $Json | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    }
    Write-Output $Json
}

function Test-PlaceholderSecret {
    param([string]$Value)
    $Normalized = $Value.Trim().ToLowerInvariant()
    foreach ($Prefix in @('sk-your-','dashscope-your-','your-','your_','replace-','replace_','change-me','changeme','example-','dummy-','sample-','${','<')) {
        if ($Normalized.StartsWith($Prefix)) { return $true }
    }
    return $Normalized.EndsWith('_here') -or $Normalized.EndsWith('-here')
}

function Test-EmbeddedSecret {
    param(
        [string]$Text,
        [System.IO.FileInfo]$File
    )
    $PrivateKeyPattern = '(?s)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{64,}\s+-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
    if ($Text -match $PrivateKeyPattern) { return $true }
    $ValuePatterns = @(
        @{ Pattern = '(?i)bearer\s+([A-Za-z0-9_.-]{20,})'; Group = 1 },
        @{ Pattern = '(?i)\b((?:sk|dashscope)-[A-Za-z0-9_-]{16,})\b'; Group = 1 }
    )
    $AssignmentExtensions = @('.py','.ps1','.cmd','.bat','.json','.yaml','.yml','.toml','.ini','.cfg','.env','.html')
    $AssignmentNames = @('config.js','configure.js','settings.js')
    if ($AssignmentExtensions -contains $File.Extension.ToLowerInvariant() -or $AssignmentNames -contains $File.Name.ToLowerInvariant()) {
        $ValuePatterns += @(
            @{ Pattern = '(?im)\b(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|password|access[_-]?token|role[_-]?key)\b\s*[''"]?\s*[:=]\s*([''"])([^''"\r\n]{8,})\1'; Group = 2 },
            @{ Pattern = '(?im)^\s*(?:export\s+|\$env:)?(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|password|access[_-]?token|role[_-]?key)\s*[:=]\s*([A-Za-z0-9_./+=-]{16,})\s*(?:#.*)?$'; Group = 1 }
        )
    }
    foreach ($Item in $ValuePatterns) {
        foreach ($Match in [regex]::Matches($Text, [string]$Item.Pattern)) {
            $Value = $Match.Groups[[int]$Item.Group].Value
            if (-not (Test-PlaceholderSecret $Value)) { return $true }
        }
    }
    return $false
}

function Test-RuntimeEndpoint {
    param([string]$Text)
    return $Text -match '(?i)https?://(?:10\.|192\.168\.|172\.(?:1[6-9]|2[0-9]|3[01])\.)'
}

function Test-DevelopmentPathText {
    param([string]$Text)
    foreach ($Marker in @(
        ("F:" + "\guoyao"),
        ("F:" + "/guoyao"),
        ("C:" + "\Users\"),
        ("migration" + "_test"),
        ("EV-Agent-Full" + "-Test"),
        ("EV-Agent-Full" + "-Zip-Restore-Test")
    )) {
        if ($Text.IndexOf($Marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
}

if ($SelfTest) {
    try {
        $Fixture = [System.IO.FileInfo]::new("scanner-fixture.yaml")
        $RuntimeEndpoint = "http://" + "192.168.5.155:9095"
        $QwenKey = "sk" + "-" + ("A" * 24)
        $AuthFixture = "Authorization: " + "Bearer " + ("B" * 24)
        $PassFixture = "password" + "=" + ("C" * 24)
        $DevelopmentPath = "F:" + "\guoyao\mvp\app.py"
        $Cases = @(
            @{ name = "runtime_endpoint"; passed = ((Test-RuntimeEndpoint $RuntimeEndpoint) -and -not (Test-EmbeddedSecret $RuntimeEndpoint $Fixture)) },
            @{ name = "qwen_api_key"; passed = (Test-EmbeddedSecret $QwenKey $Fixture) },
            @{ name = "authorization_bearer"; passed = (Test-EmbeddedSecret $AuthFixture $Fixture) },
            @{ name = "password_assignment"; passed = (Test-EmbeddedSecret $PassFixture $Fixture) },
            @{ name = "development_path"; passed = ((Test-DevelopmentPathText $DevelopmentPath) -and -not (Test-EmbeddedSecret $DevelopmentPath $Fixture)) }
        )
        if (@($Cases | Where-Object { -not $_.passed }).Count -gt 0) { throw "SECRET_SCANNER_SELF_TEST_FAILED" }
        Write-VerificationResult @{ status = "success"; scanner_self_test = "PASS"; cases = $Cases }
        exit 0
    } catch {
        Write-VerificationResult @{ status = "failed"; error_code = $_.Exception.Message }
        exit 2
    }
}

try {
    $Root = Get-PortablePackageRoot
    $Manifest = Read-PortableJson (Join-Path $Root "package_manifest.json")
    if ($Manifest.package_type -ne "PORTABLE_FULL_PROJECT") { throw "PACKAGE_TYPE_INVALID" }
    if ($Manifest.requires_python -or $Manifest.requires_conda -or $Manifest.requires_internet_for_install) {
        throw "PORTABLE_REQUIREMENTS_INVALID"
    }
    $ChecksumsPath = Join-Path $Root "checksums.sha256"
    if (-not (Test-Path -LiteralPath $ChecksumsPath -PathType Leaf)) { throw "CHECKSUM_MANIFEST_MISSING" }
    $Entries = @{}
    foreach ($Line in Get-Content -LiteralPath $ChecksumsPath -Encoding UTF8) {
        if (-not $Line.Trim()) { continue }
        if ($Line -notmatch '^([0-9A-Fa-f]{64})  (.+)$') { throw "CHECKSUM_LINE_INVALID" }
        $Relative = $Matches[2].Replace('/', [System.IO.Path]::DirectorySeparatorChar)
        $Candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $Relative))
        $RootPrefix = $Root.TrimEnd('\') + '\'
        if (-not $Candidate.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "CHECKSUM_PATH_ESCAPE" }
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { throw "CHECKSUM_FILE_MISSING:$Relative" }
        $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Candidate).Hash
        if ($Actual -ne $Matches[1].ToUpperInvariant()) { throw "CHECKSUM_MISMATCH:$Relative" }
        $Entries[$Matches[2]] = $Actual
    }
    $RuntimeRoot = Join-Path $Root 'runtime'
    $RuntimePrefix = $RuntimeRoot.TrimEnd('\') + '\'
    $StaticFiles = @(
        Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
            Where-Object {
                $_.FullName -ne $ChecksumsPath -and
                $_.Name -ne 'installed_manifest.json' -and
                -not $_.FullName.StartsWith($RuntimePrefix, [StringComparison]::OrdinalIgnoreCase)
            }
    )
    $StaticDirectories = @(
        Get-ChildItem -LiteralPath $Root -Recurse -Directory -Force |
            Where-Object {
                $_.FullName -ne $RuntimeRoot -and
                -not $_.FullName.StartsWith($RuntimePrefix, [StringComparison]::OrdinalIgnoreCase)
            }
    )
    if ($Entries.Count -ne $StaticFiles.Count) { throw "CHECKSUM_COVERAGE_MISMATCH" }

    $Archive = Join-Path $Root "payload\python\py311-win64.tar.gz"
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash -ne $Manifest.python_archive_sha256) {
        throw "PYTHON_ARCHIVE_SHA256_MISMATCH"
    }
    $ModelManifest = Read-PortableJson (Join-Path $Root "model_manifest.json")
    if ([int]$ModelManifest.models.Count -ne [int]$Manifest.model_count) { throw "MODEL_COUNT_MISMATCH" }
    foreach ($File in $ModelManifest.files) {
        $Path = Join-Path $Root ([string]$File.path).Replace('/', '\')
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "MODEL_FILE_MISSING" }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -ne $File.sha256) { throw "MODEL_FILE_SHA256_MISMATCH" }
    }
    $BgeWeight = Join-Path $Root "models\bge-small-zh-v1.5\model.safetensors"
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $BgeWeight).Hash -ne $Manifest.bge_sha256) { throw "BGE_SHA256_MISMATCH" }
    $Knowledge = Read-PortableJson (Join-Path $Root "knowledge_manifest.json")
    if ([int]$Knowledge.source_count -ne [int]$Manifest.knowledge_source_count) { throw "KNOWLEDGE_COUNT_MISMATCH" }
    foreach ($Source in $Knowledge.sources) {
        $Path = Join-Path $Root ([string]$Source.path).Replace('/', '\')
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "KNOWLEDGE_FILE_MISSING" }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -ne $Source.sha256) { throw "KNOWLEDGE_SHA256_MISMATCH" }
    }
    $RequiredFiles = @(
        "vendor\webgl\ApplicationVue\dist\index_agent.html",
        "vendor\webgl\ApplicationVue\dist\assets\index-921c2da1.js",
        "vendor\webgl\ApplicationVue\dist\assets\vendor-75261244.js",
        "vendor\webgl\ApplicationVue\dist\assets\vendor-982a78ee.css",
        "vendor\webgl\ApplicationVue\dist\datas\BOUNT_poly.shp",
        "vendor\webgl\ApplicationVue\dist\datas\BOUNT_poly.dbf",
        "vendor\webgl\ApplicationVue\dist\datas\BOUNT_poly.shx",
        "vendor\webgl\release\webgl_runtime_manifest.json",
        "frontend\webgl_agent_bootstrap.js",
        "frontend\webgl_agent_bridge.js",
        "frontend\webgl_ev_cesium_adapter.js"
    )
    foreach ($Relative in $RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $Relative) -PathType Leaf)) { throw "REQUIRED_RUNTIME_FILE_MISSING:$Relative" }
    }
    if (@($StaticDirectories | Where-Object { $_.Name -eq '.git' }).Count -gt 0) {
        throw "GIT_DIRECTORY_PRESENT"
    }
    $RetiredArchiveName = ("mvp" + "15" + ".zip")
    if (@($StaticFiles | Where-Object { $_.Name -ieq $RetiredArchiveName }).Count -gt 0) {
        throw "RETIRED_ARCHIVE_PRESENT"
    }
    if (@($StaticFiles | Where-Object { $_.Extension -ieq '.pyc' }).Count -gt 0) {
        throw "PYC_FILE_PRESENT"
    }
    if (Test-Path -LiteralPath (Join-Path $Root "config\local_llm_config.yaml") -PathType Leaf) {
        throw "PRIVATE_LLM_CONFIG_PRESENT"
    }

    $ForbiddenMarkers = @(
        ("F:" + "\guoyao"),
        ("F:" + "/guoyao"),
        ("C:" + "\Users\"),
        ("migration" + "_test"),
        ("EV-Agent-Full" + "-Test"),
        ("EV-Agent-Full" + "-Zip-Restore-Test")
    )
    $TextExtensions = @('.py','.ps1','.cmd','.bat','.js','.ts','.html','.css','.json','.yaml','.yml','.toml','.ini','.cfg','.env','.txt','.md','.pem','.key')
    $PathFindings = @()
    $RetiredArchiveReferences = @()
    $SecretFindings = @()
    $RuntimeEndpointFindings = @()
    foreach ($File in $StaticFiles) {
        if ($TextExtensions -notcontains $File.Extension.ToLowerInvariant()) { continue }
        $Text = Get-Content -LiteralPath $File.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($null -eq $Text) { continue }
        foreach ($Marker in $ForbiddenMarkers) {
            if ($Text.IndexOf($Marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $PathFindings += $File.FullName.Substring($Root.Length + 1)
            }
        }
        if ($Text.IndexOf(("mvp" + "15"), [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $RetiredArchiveReferences += $File.FullName.Substring($Root.Length + 1)
        }
        if (Test-RuntimeEndpoint $Text) {
            $RuntimeEndpointFindings += $File.FullName.Substring($Root.Length + 1)
        }
        if (Test-EmbeddedSecret $Text $File) {
            $SecretFindings += $File.FullName.Substring($Root.Length + 1)
        }
    }
    if ($PathFindings.Count -gt 0) { throw "DEVELOPMENT_PATH_FINDINGS:$($PathFindings.Count)" }
    if ($RetiredArchiveReferences.Count -gt 0) { throw "RETIRED_ARCHIVE_REFERENCE_FINDINGS:$($RetiredArchiveReferences.Count)" }
    if ($SecretFindings.Count -gt 0) { throw "SECRET_FINDINGS:$($SecretFindings.Count)" }
    $Result = @{
        status = "success"
        package_type = $Manifest.package_type
        release_id = $Manifest.release_id
        checksum_entries = $Entries.Count
        model_count = $Manifest.model_count
        knowledge_source_count = $Manifest.knowledge_source_count
        webgl_runtime_file_count = $Manifest.webgl_runtime_file_count
        retired_archive_count = 0
        development_path_findings = 0
        runtime_endpoint_findings = $RuntimeEndpointFindings.Count
        secret_findings = 0
        git_directory_count = 0
    }
    Write-VerificationResult $Result
    exit 0
} catch {
    Write-VerificationResult @{ status = "failed"; error_code = $_.Exception.Message }
    exit 2
}
