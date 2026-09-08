import { useEffect, useState } from 'react'
import {
  api, deviceImageUrl,
  type CatalogAlias, type CatalogSettings,
} from '../lib/api'

type Board = { id: string; name: string; image: string; display: string; arch: string }

const EMPTY_ALIAS: CatalogAlias = { id: '', name: '', target: '', image: '', note: '' }

export default function CatalogAdmin({ firmwareRef }: { firmwareRef: string }) {
  const [boards, setBoards] = useState<Board[]>([])
  const [settings, setSettings] = useState<CatalogSettings | null>(null)
  const [draft, setDraft] = useState<CatalogAlias>(EMPTY_ALIAS)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .adminCatalog(firmwareRef)
      .then((data) => {
        setBoards(data.devices)
        setSettings(data.settings)
      })
      .catch((err: Error) => setError(err.message))
  }, [firmwareRef])

  async function persist(next: CatalogSettings) {
    setBusy(true)
    setError(null)
    try {
      const saved = await api.saveCatalog(next)
      setSettings(saved)
      setStatus('Gespeichert.')
      setTimeout(() => setStatus(null), 2500)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (error && !settings) return <div className="notice err">{error}</div>
  if (!settings) return <p style={{ color: 'var(--muted)' }}>Lade Katalog …</p>

  const disabled = new Set(settings.disabled)
  const aliasIds = new Set(settings.aliases.map((a) => a.id))
  const needle = query.trim().toLowerCase()
  const visible = needle
    ? boards.filter((b) =>
        b.name.toLowerCase().includes(needle) || b.id.toLowerCase().includes(needle))
    : boards

  function toggle(id: string) {
    const next = new Set(disabled)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    void persist({ ...settings!, disabled: [...next] })
  }

  async function exportCatalog() {
    setBusy(true)
    setError(null)
    try {
      const data = await api.exportCatalog()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `meshhessen-katalog-${new Date().toISOString().slice(0, 10)}.json`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function importCatalog(file: File) {
    setBusy(true)
    setError(null)
    try {
      const saved = await api.importCatalog(JSON.parse(await file.text()))
      setSettings(saved)
      setStatus('Katalog übernommen.')
      setTimeout(() => setStatus(null), 3000)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function pickImage(file: File) {
    setBusy(true)
    setError(null)
    try {
      const { image } = await api.uploadCatalogImage(file)
      setDraft((current) => ({ ...current, image }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function addAlias() {
    const alias: CatalogAlias = {
      ...draft,
      id: draft.id.trim().toLowerCase(),
      name: draft.name.trim(),
    }
    void persist({
      ...settings!,
      aliases: [...settings!.aliases.filter((a) => a.id !== alias.id), alias],
    })
    setDraft(EMPTY_ALIAS)
  }

  // Wird ein vorhandener Alias bearbeitet? Dann ersetzt das Speichern ihn,
  // statt einen zweiten anzulegen.
  const editing = settings.aliases.some((a) => a.id === draft.id.trim().toLowerCase())

  const draftValid =
    /^[a-z0-9][a-z0-9_-]{1,48}$/.test(draft.id.trim().toLowerCase()) &&
    draft.name.trim().length > 0 &&
    draft.target.length > 0

  return (
    <>
      <div className="card">
        <h2>Eigene Bezeichnungen</h2>
        <p className="lead">
          Ein Alias zeigt auf ein vorhandenes Board, trägt aber einen eigenen
          Namen und ein eigenes Bild — etwa den Namen, der auf der Verpackung
          steht. Gebaut wird dieselbe Firmware wie beim Ziel, der Cache wird
          geteilt.
        </p>

        {settings.aliases.length > 0 && (
          <table className="parts" style={{ marginBottom: 18 }}>
            <thead>
              <tr><th>Bild</th><th>Name</th><th>zeigt auf</th><th /></tr>
            </thead>
            <tbody>
              {settings.aliases.map((alias) => (
                <tr key={alias.id}>
                  <td>
                    <img
                      src={deviceImageUrl(alias.image)}
                      alt=""
                      style={{ width: 40, height: 40, objectFit: 'contain' }}
                    />
                  </td>
                  <td>
                    {alias.name}
                    <div className="meta" style={{ color: 'var(--muted)', fontSize: 12 }}>
                      {alias.id}{alias.note && ` · ${alias.note}`}
                    </div>
                  </td>
                  <td className="mono">{alias.target}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button
                      className="link"
                      disabled={busy}
                      onClick={() => setDraft({ ...alias })}
                    >
                      bearbeiten
                    </button>
                    {' · '}
                    <button
                      className="link"
                      disabled={busy}
                      onClick={() => persist({
                        ...settings,
                        aliases: settings.aliases.filter((a) => a.id !== alias.id),
                      })}
                    >
                      entfernen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="field">
          <label htmlFor="alias-name">Anzeigename</label>
          <input
            id="alias-name"
            type="text"
            value={draft.name}
            placeholder="Heltec WiFi LoRa 32 Expansion Kit V2 mit LoRa 32 V4-R8"
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          />
        </div>

        <div className="field">
          <label htmlFor="alias-target">Zeigt auf</label>
          <select
            id="alias-target"
            value={draft.target}
            onChange={(event) => setDraft({ ...draft, target: event.target.value })}
          >
            <option value="">— Board wählen —</option>
            {boards.filter((b) => !aliasIds.has(b.id) || b.id === draft.target).map((b) => (
              <option key={b.id} value={b.id}>{b.name} ({b.id})</option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="alias-id">Kennung</label>
          <input
            id="alias-id"
            type="text"
            value={draft.id}
            placeholder="heltec-expansion-kit-v2"
            onChange={(event) => setDraft({ ...draft, id: event.target.value })}
          />
          <div className="help">
            Kleinbuchstaben, Ziffern, Bindestrich. Erscheint nicht in der
            Oberfläche, muss aber eindeutig sein.
          </div>
        </div>

        <div className="field">
          <label htmlFor="alias-note">Hinweis (optional)</label>
          <input
            id="alias-note"
            type="text"
            value={draft.note}
            placeholder="z. B. mit Touchscreen"
            onChange={(event) => setDraft({ ...draft, note: event.target.value })}
          />
        </div>

        <div className="field">
          <label htmlFor="alias-image">Eigenes Bild (optional)</label>
          <input
            id="alias-image"
            type="file"
            accept="image/svg+xml,image/png,image/jpeg,image/webp"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void pickImage(file)
            }}
          />
          <div className="help">
            SVG, PNG, JPEG oder WebP bis 8 MB. Fotos werden serverseitig auf
            512 px verkleinert. Ohne eigenes Bild wird das des Ziel-Boards
            verwendet.
          </div>
          {draft.image && (
            <img
              src={deviceImageUrl(draft.image)}
              alt=""
              style={{ height: 70, marginTop: 10, objectFit: 'contain' }}
            />
          )}
        </div>

        {error && <div className="notice err">{error}</div>}
        {status && <div className="notice info">{status}</div>}

        <div className="row">
          <button disabled={!draftValid || busy} onClick={addAlias}>
            {editing ? 'Änderungen speichern' : 'Alias anlegen'}
          </button>
          {(editing || draft.name || draft.image) && (
            <button className="ghost" disabled={busy} onClick={() => setDraft(EMPTY_ALIAS)}>
              Verwerfen
            </button>
          )}
        </div>
      </div>

      <div className="card">
        <h2>Modelle ausblenden</h2>
        <p className="lead">
          Ausgeblendete Boards erscheinen nicht mehr in der Auswahl.
          {' '}{disabled.size} von {boards.length} ausgeblendet.
        </p>

        <div className="field">
          <input
            type="text"
            value={query}
            placeholder="Board suchen …"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        <div className="row" style={{ marginBottom: 14 }}>
          <button className="ghost" disabled={busy} onClick={exportCatalog}>
            Katalog exportieren
          </button>
          <label className="ghost-label">
            Katalog importieren
            <input
              type="file"
              accept="application/json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void importCatalog(file)
                event.target.value = ''
              }}
            />
          </label>
          <div className="spacer" />
        </div>
        <p className="result-count">
          Export enthält Ausblendungen, Aliase und die hochgeladenen Bilder in
          einer Datei — die Bilder liegen sonst nur im Docker-Volume und fehlen
          beim Umzug.
        </p>

        <div className="board-list">
          {visible.map((board) => (
            <label key={board.id} className="board-row">
              <input
                type="checkbox"
                checked={!disabled.has(board.id)}
                disabled={busy}
                onChange={() => toggle(board.id)}
              />
              <img src={deviceImageUrl(board.image)} alt="" />
              <span className="board-name">
                {board.name}
                <span className="meta">{board.display} · {board.arch}</span>
              </span>
            </label>
          ))}
        </div>
      </div>
    </>
  )
}
