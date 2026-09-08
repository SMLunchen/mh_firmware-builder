import { useMemo, useState } from 'react'
import { isVendorDevice, type Device, type Versions } from '../lib/api'

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
  const [pinned, setPinned] = useState(false)

  // Ohne "genaue Version" bietet die Auswahl nur die neueste je Reihe an -
  // das reicht für fast alle und hält die Liste kurz.
  const options = useMemo(() => {
    if (!pinned) return versions.series.map((s) => s.latest)
    return versions.series.flatMap((s) => s.tags)
  }, [versions, pinned])

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return devices
    return devices.filter((device) =>
      device.name.toLowerCase().includes(needle) ||
      device.id.toLowerCase().includes(needle) ||
      device.arch.toLowerCase().includes(needle) ||
      device.tags.some((tag) => tag.toLowerCase().includes(needle)),
    )
  }, [devices, query])

  // Aufteilung wie im offiziellen Flasher: Geräte unterstützter Hersteller mit
  // support_level 1/2 zuerst, alles Übrige darunter. Die Reihenfolge innerhalb
  // liefert bereits die API.
  const primary = matches.filter((d) => isVendorDevice(d) && d.support_level !== 3)
  const community = matches.filter((d) => !isVendorDevice(d) || d.support_level === 3)


  const renderCard = (device: Device) => (
    <button
      key={device.id}
      className={`device ${selected?.id === device.id ? 'selected' : ''}`}
      onClick={() => onSelect(device)}
      title={device.id}
    >
      <div className="device-art">
        <img
          src={`/img/devices/${device.image || 'unknown-new.svg'}`}
          alt=""
          loading="lazy"
          onError={(event) => {
            event.currentTarget.src = '/img/devices/unknown-new.svg'
          }}
        />
      </div>
      <div className="device-body">
        <div className="name">{device.name}</div>
        <div className="meta">{device.display} · {device.arch}</div>
        {device.note && <div className="note">{device.note}</div>}
        <div className="badges">
          {device.tags.map((tag) => (
            <span key={tag} className="badge">{tag}</span>
          ))}
          {!device.supported && (
            <span className="badge muted">nicht aktiv unterstützt</span>
          )}
          {device.board_level === 'extra' && (
            <span className="badge muted">selten gebaut</span>
          )}
        </div>
      </div>
    </button>
  )

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
        <div className="notice warn">Nichts gefunden.</div>
      ) : (
        <>
          <p className="result-count">
            {matches.length} von {devices.length} Boards
          </p>

          {primary.length > 0 && (
            <div className="device-grid">
              {primary.map((device) => renderCard(device))}
            </div>
          )}

          {community.length > 0 && (
            <>
              <h3 className="group-heading">
                Von der Community unterstützte Geräte
              </h3>
              <div className="device-grid">
                {community.map((device) => renderCard(device))}
              </div>
            </>
          )}
        </>
      )}

      {selected && (
        <div className="selection-bar">
          <img
            src={`/img/devices/${selected.image || 'unknown-new.svg'}`}
            alt=""
            onError={(event) => {
              event.currentTarget.src = '/img/devices/unknown-new.svg'
            }}
          />
          <div className="selection-text">
            <strong>{selected.name}</strong>
            <div className="meta">
              {selected.display}
              {selected.has_display && selected.width > 0 &&
                ` ${selected.width}×${selected.height}`}
              {' · '}{selected.arch}
              {selected.note && ` · ${selected.note}`}
            </div>
            {selected.board_level === 'extra' && (
              <div className="warn-line">
                Wird im Firmware-Repo nur auf Anforderung gebaut — seltener
                getestet, der Build kann fehlschlagen.
              </div>
            )}
          </div>
          <button disabled={loading} onClick={onNext}>Weiter</button>
        </div>
      )}
    </div>
  )
}
