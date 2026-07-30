import express from 'express'
import { dashboardsRouter } from './routes/dashboards.js'

const app = express()
const port = process.env.PORT ?? 3001

app.use('/api', dashboardsRouter)

app.use('/api', (req, res) => {
  res.status(404).json({ error: 'Not found' })
})

app.listen(port, '127.0.0.1', () => {
  console.log(`[api] listening on http://127.0.0.1:${port}`)
})
