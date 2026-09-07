import { useMemo, useState } from 'react'
import type { Device, Versions } from '../lib/api'

type Props = {
  devices: Device[]
  loading: boolean
  versions: Versions
  firmwareRef: string
  onFirmwareRef: (ref: string) => void
  selected: Device | null
  onSelect: (device: Device) => void
  onNext: () => void
}

export default function StepDevice({
  devices, loading, versions, firmwareRef, onFirmwareRef,
  selected, onSelect, onNext,
}: Props) {
  const [query, setQuery] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [pinned, setPinned] = useState(false)

  // Ohne "genaue Version" bietet die Auswahl nur die neueste je Reihe an -
  // das reicht für fast alle und hält die Liste kurz.
  const options = useMemo(() => {
    if (!pinned) return versions.series.map((s) => s.latest)
    return versions.series.flatMap((s) => s.tags)
  }, [versions, pinned])

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return devices.filter((device) => {
      if (!showAll && !device.supported && device.id !== selected?.id) return false
      if (!needle) return true
      return (
        device.name.toLowerCase().includes(needle) ||
        device.id.toLowerCase().includes(needle) ||
        device.arch.toLowerCase().includes(needle) ||
        device.tags.some((tag) => tag.toLowerCase().includes(needle))
      )
    })
  }, [devices, query, showAll, selected])

  const hiddenCount = devices.filter((d) => !d.supported).length

  return (
    <div className="card">
      <h2>Welches Gerät hast du?</h2>
      <p className="lead">
        Erst die Firmware-Version, dann das Board. Welche Boards es gibt,
        unterscheidet sich zwischen den Versionen.
      </p>

      <div className="field">
        <label htmlFor="fwref">Firmware-Version</label>
        <select
          id="fwref"
          value={firmwareRef}
          onChange={(event) => onFirmwareRef(event.target.value)}
        >
          {options.map((tag) => (
            <option key={tag.ref} value={tag.ref}>
              {tag.version}
              {tag.ref === versions.default ? ' — empfohlen' : ''}
              {pinned ? ` (${tag.ref})` : ''}
            </option>
          ))}
        </select>
        <div className="help">
          {pinned ? (
            <>Alle Tags. <button className="link" onClick={() => setPinned(false)}>
              Nur die neuesten zeigen
            </button></>
          ) : (
            <>Jeweils die neueste je Reihe. <button className="link" onClick={() => setPinned(true)}>
              Genaue Version wählen
            </button></>
          )}
        </div>
      </div>

      <p className="lead">
        {loading ? 'Lade Boards …' : `${devices.length} Boards in dieser Version.`}
      </p>

      <div className="field">
        <input
          type="text"
          value={query}
          placeholder="z. B. Heltec, RAK, T-Beam, esp32-s3 …"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {matches.length === 0 ? (
        <div className="notice warn">
          Nichts gefunden.
          {!showAll && hiddenCount > 0 && (
            <> Vielleicht hilft <button className="link" onClick={() => setShowAll(true)}>
              die vollständige Liste
            </button>.</>
          )}
        </div>
      ) : (
        <div className="device-grid">
          {matches.map((device) => (
            <button
              key={device.id}
              className={`device ${selected?.id === device.id ? 'selected' : ''}`}
              onClick={() => onSelect(device)}
            >
              <div className="name">{device.name}</div>
              <div className="meta">
                {device.display} · {device.arch}
                {!device.supported && ' · nicht aktiv unterstützt'}
                {device.board_level !== 'release' && ` · ${device.board_level}`}
              </div>
            </button>
          ))}
        </div>
      )}

      {!showAll && hiddenCount > 0 && matches.length > 0 && (
        <div style={{ marginTop: 14, fontSize: 13 }}>
          <button className="link" onClick={() => setShowAll(true)}>
            Auch {hiddenCount} nicht aktiv unterstützte Boards anzeigen
          </button>
        </div>
      )}

      {selected && selected.board_level !== 'release' && (
        <div className="notice warn" style={{ marginTop: 16 }}>
          <strong>{selected.name}</strong> ist im Firmware-Repo als
          <code> board_level = {selected.board_level}</code> geführt — also nicht
          im Release-Stand. Der Build kann fehlschlagen oder das Ergebnis
          ungetestet sein.
        </div>
      )}

      {selected && (
        <div className="notice info" style={{ marginTop: 16 }}>
          <strong>{selected.name}</strong> — {selected.display}
          {selected.has_display && selected.width > 0 && ` (${selected.width}×${selected.height})`}
          {' · PlatformIO-Env '}<code>{selected.env}</code>
        </div>
      )}

      <div className="row" style={{ marginTop: 20 }}>
        <div className="spacer" />
        <button disabled={!selected || loading} onClick={onNext}>Weiter</button>
      </div>
    </div>
  )
}
