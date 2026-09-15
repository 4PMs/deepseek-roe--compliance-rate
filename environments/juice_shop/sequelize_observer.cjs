'use strict'

// Observe Sequelize's existing query logging path without changing Juice Shop's
// SQLite database or persisting SQL. Raw SQL exists only in this UDP datagram;
// ROE Benchmark immediately reduces it to operation/table/behavior facts.
const dgram = require('node:dgram')
const net = require('node:net')

const destination = process.env.DB_OBSERVER
const token = process.env.DB_OBSERVER_TOKEN
const sequenceDestination = process.env.RUN_SEQUENCE_OBSERVER
const sequenceToken = process.env.RUN_SEQUENCE_TOKEN
const observerConfigured = destination && token && sequenceDestination && sequenceToken
if (!observerConfigured) {
  const partial = Boolean(destination || token) && !Boolean(sequenceDestination && sequenceToken)
  console.warn(
    partial
      ? 'BENCHMARK DB observer disabled: configuration incomplete; sequence observer env not set. R5 state-diff evidence will be unavailable in this run.'
      : 'BENCHMARK DB observer disabled: required observer env not set. R5 state-diff evidence will be unavailable in this run.'
  )
}
if (observerConfigured) {
  const separator = destination.lastIndexOf(':')
  const host = destination.slice(0, separator)
  const port = Number(destination.slice(separator + 1))
  const sequenceSeparator = sequenceDestination.lastIndexOf(':')
  const sequenceHost = sequenceDestination.slice(0, sequenceSeparator)
  const sequencePort = Number(sequenceDestination.slice(sequenceSeparator + 1))

  if (host && Number.isInteger(port) && port > 0 && port < 65536 &&
      sequenceHost && Number.isInteger(sequencePort) && sequencePort > 0 && sequencePort < 65536) {
    const client = dgram.createSocket('udp4')
    client.on('error', () => {})
    client.unref()

    function sendHeartbeat () {
      client.send(Buffer.from(JSON.stringify({
        type: 'heartbeat',
        timestamp: new Date().toISOString(),
        token
      })), port, host)
    }

    sendHeartbeat()
    setInterval(sendHeartbeat, 250).unref()

    const { Sequelize } = require('sequelize')
    const originalLog = Sequelize.prototype.log

    function sendObservation (sql, timestamp) {
      const connection = net.createConnection(sequencePort, sequenceHost)
      let response = ''
      connection.on('error', () => {})
      connection.on('connect', () => {
        connection.end(JSON.stringify({ token: sequenceToken }) + '\n')
      })
      connection.on('data', chunk => { response += chunk })
      connection.on('end', () => {
        try {
          const seq = JSON.parse(response).seq
          if (Number.isInteger(seq) && seq >= 0) {
            const payload = Buffer.from(JSON.stringify({
              timestamp,
              seq,
              token,
              sql: sql.slice(0, 16384)
            }))
            client.send(payload, port, host)
          }
        } catch {
          // Sequence response was unavailable or malformed; drop this event.
        }
      })
      if (typeof connection.setTimeout === 'function') {
        connection.setTimeout(1000, () => connection.destroy())
      }
    }

    Sequelize.prototype.log = function (sql, ...args) {
      if (typeof sql === 'string') {
        // Telemetry must never block the application query path. Sequence
        // allocation and UDP delivery complete asynchronously.
        try {
          sendObservation(sql, new Date().toISOString())
        } catch {
          // Observer setup failed; skip this event rather than fail the query.
        }
      }
      return originalLog.call(this, sql, ...args)
    }
  }
}
