import { fileURLToPath } from 'node:url'
import path from 'node:path'

const thisDir = path.dirname(fileURLToPath(import.meta.url))

export const CONTENT_ROOT = path.resolve(thisDir, '..', '..', 'content')
