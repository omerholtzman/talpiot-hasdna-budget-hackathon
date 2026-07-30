import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const thisDir = path.dirname(fileURLToPath(import.meta.url))
const distDir = path.resolve(thisDir, '..', 'dist')

fs.copyFileSync(path.join(distDir, 'index.html'), path.join(distDir, '404.html'))

console.log('[copy-404] wrote dist/404.html')
