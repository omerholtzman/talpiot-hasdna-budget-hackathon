import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { CONTENT_ROOT } from '../server/lib/contentRoot.js'
import { listDashboards, readDashboard } from '../server/lib/scanDashboards.js'

const thisDir = path.dirname(fileURLToPath(import.meta.url))
const API_OUT_ROOT = path.resolve(thisDir, '..', 'public', 'api')

async function main() {
  const dashboards = await listDashboards()

  await fs.rm(API_OUT_ROOT, { recursive: true, force: true })
  await fs.mkdir(API_OUT_ROOT, { recursive: true })

  await fs.writeFile(
    path.join(API_OUT_ROOT, 'dashboards-list.json'),
    JSON.stringify({ dashboards })
  )

  for (const { slug } of dashboards) {
    const filePath = path.join(CONTENT_ROOT, `${slug}.md`)
    const detail = await readDashboard(filePath, slug)

    const outPath = path.join(API_OUT_ROOT, 'dashboards', `${slug}.json`)
    await fs.mkdir(path.dirname(outPath), { recursive: true })
    await fs.writeFile(outPath, JSON.stringify(detail))
  }

  console.log(`[generate-static-api] wrote ${dashboards.length} dashboard(s) to ${API_OUT_ROOT}`)
}

main().catch((error) => {
  console.error('[generate-static-api] failed:', error)
  process.exitCode = 1
})
