import { useRef, useState } from 'react'
import { api, type Manifest } from '../lib/api'
import { flash, serialSupported, type FlashProgress } from '../lib/flasher'

type Props = {
  manifest: Manifest
  onBack: () => void
  onNext: () => void
}

export default function StepFlash({ manifest, onBack, onNext }: Props) {
  const [lines, setLines] = useState<string[]>([])
  const [progress, setProgress] = useState<FlashProgress | null>(null)
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  const supported = serialSupported()
  const serialFlashable = manifest.parts.some((p) => p.offset !== null)

  function log(line: string) {
    setLines((prev) => [...prev.slice(-2000), line])
    queueMicrotask(() => {
      const el = logRef.current
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  async function run() {
    setState('busy')
    setError(null)
    try {
      await flash(manifest, log, setProgress)
      setState('done')
    } catch (err) {
      setError((err as Error).message)
      setState('error')
    }
  }

  return (
    <div className="card">
      <h2>Firmware aufspielen</h2>
      <p className="lead">
        Schließe dein {manifest.device_name} per USB an und starte den Vorgang.
        Der Browser fragt dann, welcher Port genutzt werden soll.
      </p>

      {!supported && (
        <div className="notice err">
          Dieser Browser unterstützt Web Serial nicht. Nutze Chrome oder Edge —
          und rufe die Seite über HTTPS oder localhost auf.
        </div>
      )}

      {!serialFlashable && (
        <div className="notice warn">
          {manifest.device_name} wird nicht seriell geflasht. Lade die Datei herunter
          und kopiere sie im Bootloader-Modus auf das Gerät:
          {manifest.parts.map((part) => (
            <div key={part.name} style={{ marginTop: 6 }}>
              <a href={api.artifactUrl(manifest.cache_key, part.name)}>{part.name}</a>
            </div>
          ))}
        </div>
      )}

      {state === 'idle' && serialFlashable && (
        <div className="notice info">
          Wir schreiben Bootloader, Partitionstabelle, OTA-Zeiger, App und Dateisystem
          einzeln an ihre echten Adressen — nicht als factory.bin. Nur so kommt auch
          das Dateisystem mit dem Startbildschirm mit aufs Gerät.
        </div>
      )}

      {progress && state === 'busy' && (
        <>
          <div className="bar"><span style={{ width: `${progress.percent}%` }} /></div>
          <div style={{ color: 'var(--muted)', fontSize: 13 }}>
            {progress.phase} — Datei {progress.fileIndex + 1}/{progress.fileCount} ({progress.percent}%)
          </div>
        </>
      )}

      {state === 'done' && (
        <div className="notice info" style={{ borderColor: 'var(--green-dark)', color: 'var(--green)' }}>
          ✓ Fertig. Das Gerät startet gerade neu und zeigt den MeshHessen-Startbildschirm.
        </div>
      )}

      {error && <div className="notice err">{error}</div>}

      {lines.length > 0 && (
        <div className="log" ref={logRef} style={{ height: 220 }}>
          {lines.map((line, index) => (
            <div key={index} className={line.startsWith('✓') ? 'ok' : undefined}>{line}</div>
          ))}
        </div>
      )}

      <div className="row" style={{ marginTop: 18 }}>
        <button className="ghost" onClick={onBack} disabled={state === 'busy'}>Zurück</button>
        <div className="spacer" />
        {state !== 'done' && serialFlashable && (
          <button onClick={run} disabled={!supported || state === 'busy'}>
            {state === 'busy' ? 'Flashe …' : state === 'error' ? 'Nochmal versuchen' : 'Jetzt flashen'}
          </button>
        )}
        {(state === 'done' || !serialFlashable) && (
          <button onClick={onNext}>Weiter</button>
        )}
      </div>
    </div>
  )
}
