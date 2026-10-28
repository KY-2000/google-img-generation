param(
    [Parameter(Mandatory = $true)][string]$Template,
    [Parameter(Mandatory = $true)][string]$RowsJson,
    [Parameter(Mandatory = $true)][string]$Output
)

$ErrorActionPreference = 'Stop'
$templatePath = (Resolve-Path -LiteralPath $Template).Path
$rowsPath = (Resolve-Path -LiteralPath $RowsJson).Path
$outputPath = [System.IO.Path]::GetFullPath($Output)
$outputDirectory = [System.IO.Path]::GetDirectoryName($outputPath)
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
Copy-Item -LiteralPath $templatePath -Destination $outputPath -Force

$headers = @(
    'Filename', 'Image Name', 'Description', 'Category 1', 'Category 2', 'Category 3',
    'keywords', 'Free', 'W-EL', 'P-EL', 'SR-EL', 'SR-Price', 'Editorial', 'MR doc Ids', 'Pr Docs'
)
$parsedRows = Get-Content -Raw -LiteralPath $rowsPath | ConvertFrom-Json
$rows = @($parsedRows | ForEach-Object { $_ })
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$workbook = $null
$worksheet = $null
try {
    $workbook = $excel.Workbooks.Open($outputPath)
    $worksheet = $workbook.Worksheets.Item('Enter data here')
    $legend = $workbook.Worksheets.Item('Image Legend')
    foreach ($requiredCode in @('212', '163', '166')) {
        $found = $legend.UsedRange.Find($requiredCode)
        if (-not $found) { throw "Dreamstime category code $requiredCode is absent from Image Legend" }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($found)
    }
    for ($column = 1; $column -le $headers.Count; $column++) {
        $actual = [string]$worksheet.Cells.Item(1, $column).Text
        if ($actual -ne $headers[$column - 1]) {
            throw "Unexpected template header in column $column`: '$actual'"
        }
    }
    $lastRow = [Math]::Max(2, $worksheet.UsedRange.Rows.Count)
    $worksheet.Range($worksheet.Cells.Item(2, 1), $worksheet.Cells.Item($lastRow, 15)).ClearContents()
    for ($index = 0; $index -lt $rows.Count; $index++) {
        $rowNumber = $index + 2
        for ($column = 1; $column -le $headers.Count; $column++) {
            $header = $headers[$column - 1]
            $worksheet.Cells.Item($rowNumber, $column).NumberFormat = '@'
            $worksheet.Cells.Item($rowNumber, $column).Value2 = [string]$rows[$index].$header
        }
    }
    $workbook.Save()
    Write-Output "Dreamstime XLS rows=$($rows.Count) output=$outputPath"
}
finally {
    if ($workbook) { $workbook.Close($true) }
    if ($worksheet) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($worksheet) }
    if ($legend) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($legend) }
    if ($workbook) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook) }
    $excel.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
