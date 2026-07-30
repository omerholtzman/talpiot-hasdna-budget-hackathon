import fs from 'node:fs/promises'
import path from 'node:path'
import matter from 'gray-matter'
import { CONTENT_ROOT } from './contentRoot.js'

async function collectMarkdownFiles(dir) {
  let entries
  try {
    entries = await fs.readdir(dir, { withFileTypes: true })
  } catch (error) {
    if (error.code === 'ENOENT') return []
    throw error
  }

  const files = await Promise.all(
    entries.map(async (entry) => {
      const entryPath = path.join(dir, entry.name)
      if (entry.isDirectory()) return collectMarkdownFiles(entryPath)
      if (entry.isFile() && entry.name.endsWith('.md')) return [entryPath]
      return []
    })
  )

  return files.flat()
}

function toSlug(absoluteFilePath) {
  const relativePath = path.relative(CONTENT_ROOT, absoluteFilePath)
  return relativePath.slice(0, -'.md'.length).split(path.sep).join('/')
}

function toDateString(value) {
  if (!value) return null
  if (value instanceof Date) return value.toISOString().slice(0, 10)
  return String(value)
}

function toMeta(slug, data) {
  const created = toDateString(data.created)
  return {
    slug,
    title: data.title ?? slug,
    created,
    updated: toDateString(data.updated) ?? created,
    model: data.model ?? null,
    path: data.path ?? null,
  }
}

export async function listDashboards() {
  const files = await collectMarkdownFiles(CONTENT_ROOT)

  const dashboards = await Promise.all(
    files.map(async (filePath) => {
      try {
        const raw = await fs.readFile(filePath, 'utf-8')
        const { data } = matter(raw)
        return toMeta(toSlug(filePath), data)
      } catch (error) {
        console.error(`[dashboards] skipping unparsable file ${filePath}:`, error.message)
        return null
      }
    })
  )

  return dashboards
    .filter((dashboard) => dashboard !== null)
    .sort((a, b) => (b.updated ?? '').localeCompare(a.updated ?? ''))
}

export async function readDashboard(absoluteFilePath, slug) {
  const raw = await fs.readFile(absoluteFilePath, 'utf-8')
  const { data, content } = matter(raw)
  return { ...toMeta(slug, data), content: content.trim() }
}
