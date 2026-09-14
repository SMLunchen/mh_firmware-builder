import { useEffect, useRef, useState } from 'react'
import { api, streamLogs, type Build, type Device } from '../lib/api'
import type { SolveProgress } from '../lib/pow'

type Props = {
  device: Device
  name: string
  overrides: Record<string, string>
  firmwareRef: string
  build: Build | null
  onBuild: (build: Build) => void
  onBack: () => void
  onNext: () => void
}

const HINTS = [
  'PlatformIO lädt die Toolchain und übersetzt rund 1.500 Quelldateien.',
  'Der Splash wird als PNG ins LittleFS-Image gepackt.',
  'Du kannst die Seite offen lassen — der Build läuft auf dem Server weiter.',
  'Halte schon mal dein USB-Kabel bereit.',
]

export default function StepBuild({
  device, name, overrides, firmwareRef, build, onBuild, onBack, onNext,
}: Props) {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [hint, setHint] = useState(0)
  const [solving, setSolving] = useState<SolveProgress | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const logRef = useRef<HTMLDivElement>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current || build?.status === 'done') return
    started.current = true

    let stop: (() => void) | undefined
    api
      .startBuild(
        device.id,
        name || undefined,
        Object.keys(overrides).length ? overrides : undefined,
        firmwareRef || undefined,
        setSolving,
      )
      .then((created) => {
        onBuild(created)
        if (created.status === 'done') {
          setLines(['Fertige Firmware aus dem Cache — kein Neubau nötig.'])
          return
        }
        stop = streamLogs(
          created.id,
          (line) => setLines((prev) => [...prev.slice(-4000), line]),
          (finished) => {
            onBuild(finished)
            if (finished.status === 'error') setError(finished.error ?? 'Build fehlgeschlagen')
          },
          (message) => setError(message),
        )
      })
      .catch((err: Error) => setError(err.message))

    return () => stop?.()
    // Der Build wird genau einmal angestoßen; started.current schützt vor StrictMode.
  }, [])

  useEffect(() => {
    if (build?.status === 'done' || build?.status === 'error' || error) return
    const timer = setInterval(() => setElapsed((value) => value + 1), 1000)
    const hints = setInterval(() => setHint((value) => (value + 1) % HINTS.length), 9000)
    return () => {
      clearInterval(timer)
      clearInterval(hints)
    }
  }, [build?.status, error])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  const done = build?.status === 'done'
  const failed = build?.status === 'error' || Boolean(error)
  const running = !done && !failed

  return (
    <div className="card">
      <h2>{done ? 'Firmware ist fertig' : failed ? 'Build fehlgeschlagen' : 'Firmware wird gebaut'}</h2>
      <p className="lead">
        {device.name} · {build?.firmware_ref ?? firmwareRef}
        {name && ` · personalisiert für ${name}`}
      </p>

      {solving && (
        <div className="notice info">
          Kurze Sicherheitsabfrage, damit automatisierte Anfragen die
          Rechenleistung nicht blockieren — dauert meist unter einer Sekunde.
          {solving.attempts > 0 &&
            ` (${(solving.attempts / 1000).toFixed(0)}k Versuche)`}
        </div>
      )}

      {running && (
        <>
          <div className="notice info">
            {HINTS[hint]} — bisher {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')} min
          </div>
          <div className="bar"><span style={{ width: '100%', opacity: 0.35 }} /></div>
        </>
      )}

      {failed && <div className="notice err">{error ?? build?.error}</div>}

      <div className="log" ref={logRef}>
        {lines.length === 0 && running && 'Build wird gestartet …'}
        {lines.map((line, index) => (
          <div
            key={index}
            className={line.startsWith('✗') ? 'err' : line.startsWith('✓') ? 'ok' : undefined}
          >
            {line}
          </div>
        ))}
      </div>

      {done && build?.manifest && (
        <table className="parts">
          <thead>
            <tr><th>Datei</th><th>Adresse</th><th>Größe</th><th>Rolle</th></tr>
          </thead>
          <tbody>
            {build.manifest.parts.map((part) => (
              <tr key={part.name}>
                <td className="mono">{part.name}</td>
                <td className="mono">
                  {part.offset === null ? '—' : `0x${part.offset.toString(16)}`}
                </td>
                <td>{Math.round(part.size / 1024)} KB</td>
                <td>{part.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row" style={{ marginTop: 18 }}>
        <button className="ghost" onClick={onBack} disabled={running}>Zurück</button>
        <div className="spacer" />
        <button onClick={onNext} disabled={!done}>Gerät anschließen und flashen</button>
      </div>
    </div>
  )
}
