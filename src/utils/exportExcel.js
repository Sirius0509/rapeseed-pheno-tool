import { strToU8, zipSync } from 'fflate'

const headers = [
  '样品编号',
  '基因型',
  '处理',
  '重复',
  '株高_cm',
  '主花序高_cm',
  '主花序长度_cm',
  '一级分枝数',
  '平均分支角',
  '最大分支角',
  '最小分支角',
  '备注',
  '测量日期',
]

const fields = [
  'sampleId',
  'genotype',
  'treatment',
  'replicate',
  'plantHeightCm',
  'inflorescenceHeightCm',
  'inflorescenceLengthCm',
  'branchCount',
  'branchAngleAvg',
  'branchAngleMax',
  'branchAngleMin',
  'notes',
  'measuredAt',
]

export function exportSamples(samples) {
  const rows = [
    headers,
    ...samples.map((sample) =>
      fields.map((field) => {
        if (field in sample.metrics) return sample.metrics[field]
        return sample[field] ?? ''
      }),
    ),
  ]
  const files = createWorkbookFiles(rows)
  const zipped = zipSync(files)
  const blob = new Blob([zipped], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `rapeseed-pheno-${new Date().toISOString().slice(0, 10)}.xlsx`
  link.click()
  URL.revokeObjectURL(link.href)
}

export function exportSiliqueRecords(records) {
  const siliqueHeaders = [
    '材料编号',
    '样品编号',
    '重复',
    '角果编号',
    '角果长度_mm',
    '籽粒数',
    '粒数每cm',
    '识别方式',
    '照片名称',
    '云端链接',
    '备注',
    '测量日期',
  ]
  const rows = [
    siliqueHeaders,
    ...records.map((record) => [
      record.genotype,
      record.sampleId,
      record.replicate,
      record.siliqueId,
      record.siliqueLengthMm,
      record.seedCount,
      record.seedsPerCm,
      record.method,
      record.imageName,
      record.cloudUrl,
      record.notes,
      record.measuredAt,
    ]),
  ]
  downloadWorkbook(rows, `rapeseed-silique-${new Date().toISOString().slice(0, 10)}.xlsx`, '角果籽粒数据')
}

function downloadWorkbook(rows, filename, sheetName = '油菜表型数据') {
  const files = createWorkbookFiles(rows, sheetName)
  const zipped = zipSync(files)
  const blob = new Blob([zipped], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}

function createWorkbookFiles(rows, sheetName = '油菜表型数据') {
  return {
    '[Content_Types].xml': xmlFile(`<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>`),
    '_rels/.rels': xmlFile(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`),
    'xl/workbook.xml': xmlFile(`<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="${escapeXml(sheetName)}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>`),
    'xl/_rels/workbook.xml.rels': xmlFile(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`),
    'xl/styles.xml': xmlFile(`<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>`),
    'xl/worksheets/sheet1.xml': xmlFile(createSheetXml(rows)),
  }
}

function createSheetXml(rows) {
  const body = rows
    .map((row, rowIndex) => {
      const cells = row
        .map((value, columnIndex) => createCell(value, columnName(columnIndex) + (rowIndex + 1)))
        .join('')
      return `<row r="${rowIndex + 1}">${cells}</row>`
    })
    .join('')

  return `<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>${body}</sheetData>
</worksheet>`
}

function createCell(value, ref) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<c r="${ref}"><v>${value}</v></c>`
  }
  return `<c r="${ref}" t="inlineStr"><is><t>${escapeXml(String(value ?? ''))}</t></is></c>`
}

function columnName(index) {
  let name = ''
  let n = index + 1
  while (n > 0) {
    const rem = (n - 1) % 26
    name = String.fromCharCode(65 + rem) + name
    n = Math.floor((n - 1) / 26)
  }
  return name
}

function xmlFile(content) {
  return strToU8(content)
}

function escapeXml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;')
}
