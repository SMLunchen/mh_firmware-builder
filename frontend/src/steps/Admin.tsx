import { useEffect, useState } from 'react'
import { api, auth, type Device, type OverrideSpec } from '../lib/api'

type Props = {
  devices: Device[]
  isAdmin: boolean
  onAuth: (ok: boolean) => void
  onSpecialBuild: (device: Device, name: string, overrides: Record<string, string>) => void
}

export default function Admin({ devices, isAdmin, onAuth, onSpecialBuild }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [specs, setSpecs] = useState<OverrideSpec[]>([])
  const [site, setSite] = useState<Record<string, string>>({})

  const [deviceId, setDeviceId] = useState('')
  const [name, setName] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!isAdmin) return
    api
      .adminSchema()
      .then((data) => {
        setSpecs(data.overrides)
        setSite(data.site)
      })
      .catch((err: Error) => {
        setError(err.message)
        auth.set(null)
        onAuth(false)
      })
  }, [isAdmin])

  async function login() {
    setError(null)
    try {
      const { token } = await api.login(password)
      auth.set(token)
      setPassword('')
      onAuth(true)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  function logout() {
    auth.set(null)
    onAuth(false)
    setSpecs([])
  }

  if (!isAdmin) {
    return (
      <div className="card">
        <h2>Anmeldung</h2>
        <p className="lead">
          Der Admin-Bereich baut Firmware für Spezialprojekte mit abweichenden
          Funkparametern. Öffentliche Builds bleiben davon unberührt.
        </p>
        <div className="field">
          <label htmlFor="pw">Passwort</label>
          <input
            id="pw"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && login()}
          />
        </div>
        {error && <div className="notice err">{error}</div>}
        <button onClick={login} disabled={!password}>Anmelden</button>
      </div>
    )
  }

  const device = devices.find((d) => d.id === deviceId) ?? null
  const active = Object.fromEntries(
    Object.entries(values).filter(([, value]) => value.trim().length > 0),
  )

  return (
    <>
      <div className="card">
        <div className="row">
          <h2 style={{ margin: 0 }}>Spezialbuild</h2>
          <div className="spacer" />
          <button className="ghost" onClick={logout}>Abmelden</button>
        </div>
        <p className="lead" style={{ marginTop: 8 }}>
          Nur die hier ausgefüllten Felder weichen vom MeshHessen-Standard ab.
          Leere Felder behalten den Standardwert. Der Build bekommt einen eigenen
          Cache-Eintrag und kann nie mit einem öffentlichen Build kollidieren.
        </p>

        <div className="field">
          <label htmlFor="dev">Gerät</label>
          <select id="dev" value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
            <option value="">— auswählen —</option>
            {devices.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="projname">Projekt-/Nodename für den Splash (optional)</label>
          <input
            id="projname"
            type="text"
            maxLength={24}
            value={name}
            placeholder="z. B. Relais Feldberg"
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        {specs.map((spec) => (
          <div className="field" key={spec.key}>
            <label htmlFor={spec.key}>{spec.label}</label>
            {spec.type === 'enum' ? (
              <select
                id={spec.key}
                value={values[spec.key] ?? ''}
                onChange={(event) => setValues({ ...values, [spec.key]: event.target.value })}
              >
                <option value="">— Standard —</option>
                {spec.options?.map((option) => (
                  <option key={option} value={option}>
                    {option.split('_').slice(-2).join('_')}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={spec.key}
                type={spec.type === 'int' ? 'number' : 'text'}
                min={spec.min}
                max={spec.max}
                placeholder={spec.placeholder ?? 'Standard'}
                value={values[spec.key] ?? ''}
                onChange={(event) => setValues({ ...values, [spec.key]: event.target.value })}
              />
            )}
            {spec.help && <div className="help">{spec.help}</div>}
          </div>
        ))}

        {Object.keys(active).length > 0 && (
          <div className="notice warn">
            Abweichend: {Object.keys(active).map((key) =>
              specs.find((s) => s.key === key)?.label ?? key).join(', ')}
          </div>
        )}

        <button
          disabled={!device}
          onClick={() => device && onSpecialBuild(device, name.trim(), active)}
        >
          Spezialbuild starten
        </button>
      </div>

      <div className="card">
        <h2>Betriebsparameter</h2>
        <p className="lead">Gelten für alle Builds und die Weiterleitung am Ende.</p>
        <table className="parts">
          <tbody>
            <tr><td>Splash-Schriftzug</td><td className="mono">{site.splash_prefix}</td></tr>
            <tr><td>Schnellkonfiguration</td><td className="mono">{site.config_url}</td></tr>
          </tbody>
        </table>
      </div>
    </>
  )
}
