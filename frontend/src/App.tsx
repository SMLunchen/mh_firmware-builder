import { useEffect, useState } from 'react'
import { api, auth, type Build, type Device, type SiteConfig, type Versions } from './lib/api'
import StepDevice from './steps/StepDevice'
import StepPersonalize from './steps/StepPersonalize'
import StepBuild from './steps/StepBuild'
import StepFlash from './steps/StepFlash'
import StepConfigure from './steps/StepConfigure'
import Admin from './steps/Admin'

const STEP_LABELS = ['Gerät', 'Personalisierung', 'Build', 'Flashen', 'Konfiguration']

export default function App() {
  const [config, setConfig] = useState<SiteConfig | null>(null)
  const [versions, setVersions] = useState<Versions | null>(null)
  const [devices, setDevices] = useState<Device[]>([])
  const [firmwareRef, setFirmwareRef] = useState('')
  const [devicesLoading, setDevicesLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [step, setStep] = useState(0)
  const [device, setDevice] = useState<Device | null>(null)
  const [name, setName] = useState('')
  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [build, setBuild] = useState<Build | null>(null)

  const [isAdmin, setIsAdmin] = useState(Boolean(auth.token))
  const [showAdmin, setShowAdmin] = useState(location.hash === '#admin')

  useEffect(() => {
    Promise.all([api.config(), api.versions()])
      .then(([cfg, vers]) => {
        setConfig(cfg)
        setVersions(vers)
        setFirmwareRef(vers.default)
      })
      .catch((err: Error) => setLoadError(err.message))
  }, [])

  // Welche Boards es gibt, unterscheidet sich je Firmware-Version.
  useEffect(() => {
    if (!firmwareRef) return
    setDevicesLoading(true)
    api
      .devices(firmwareRef)
      .then((result) => {
        setDevices(result.devices)
        // Ausgewähltes Board fällt weg, wenn es die neue Version nicht kennt.
        setDevice((current) =>
          current && result.devices.some((d) => d.id === current.id) ? current : null,
        )
      })
      .catch((err: Error) => setLoadError(err.message))
      .finally(() => setDevicesLoading(false))
  }, [firmwareRef])

  useEffect(() => {
    const onHash = () => setShowAdmin(location.hash === '#admin')
    addEventListener('hashchange', onHash)
    return () => removeEventListener('hashchange', onHash)
  }, [])

  function reset() {
    setStep(0)
    setFirmwareRef(versions?.default ?? '')
    setDevice(null)
    setName('')
    setOverrides({})
    setBuild(null)
  }

  if (loadError) {
    return (
      <div className="shell">
        <div className="notice err">Backend nicht erreichbar: {loadError}</div>
      </div>
    )
  }
  if (!config || !versions) {
    return <div className="shell"><p style={{ color: 'var(--muted)' }}>Lade …</p></div>
  }

  if (showAdmin) {
    return (
      <div className="shell">
        <header className="top">
          <h1>MeshHessen Firmware-Builder — Admin</h1>
          <button className="ghost" onClick={() => { location.hash = '' }}>Zurück</button>
        </header>
        <Admin
          devices={devices}
          isAdmin={isAdmin}
          onAuth={(ok) => setIsAdmin(ok)}
          onSpecialBuild={(dev, buildName, ovr) => {
            setDevice(dev)
            setName(buildName)
            setOverrides(ovr)
            setBuild(null)
            setStep(2)
            location.hash = ''
          }}
        />
      </div>
    )
  }

  // Geräte ohne Display überspringen die Personalisierung.
  const skipsPersonalize = device !== null && !device.has_display
  const visibleSteps = STEP_LABELS.filter((_, i) => !(i === 1 && skipsPersonalize))

  return (
    <div className="shell">
      <header className="top">
        <div>
          <h1>MeshHessen Firmware-Builder</h1>
          <div className="ref">
            Firmware {firmwareRef || config.firmware_ref}
            {isAdmin && <> · <span className="admin-badge">Admin</span></>}
          </div>
        </div>
        {config.admin_enabled && (
          <button className="ghost" onClick={() => { location.hash = '#admin' }}>
            Admin
          </button>
        )}
      </header>

      <div className="steps">
        {visibleSteps.map((label) => {
          const index = STEP_LABELS.indexOf(label)
          return (
            <div
              key={label}
              className={`step ${index === step ? 'active' : ''} ${index < step ? 'done' : ''}`}
            >
              {label}
            </div>
          )
        })}
      </div>

      {step === 0 && (
        <StepDevice
          devices={devices}
          loading={devicesLoading}
          versions={versions}
          firmwareRef={firmwareRef}
          onFirmwareRef={setFirmwareRef}
          selected={device}
          onSelect={setDevice}
          onNext={() => setStep(device?.has_display ? 1 : 2)}
        />
      )}

      {step === 1 && device && (
        <StepPersonalize
          device={device}
          config={config}
          name={name}
          onName={setName}
          onBack={() => setStep(0)}
          onNext={() => setStep(2)}
        />
      )}

      {step === 2 && device && (
        <StepBuild
          device={device}
          name={name}
          overrides={overrides}
          firmwareRef={firmwareRef}
          build={build}
          onBuild={setBuild}
          onBack={() => setStep(device.has_display ? 1 : 0)}
          onNext={() => setStep(3)}
        />
      )}

      {step === 3 && build?.manifest && (
        <StepFlash
          manifest={build.manifest}
          onBack={() => setStep(2)}
          onNext={() => setStep(4)}
        />
      )}

      {step === 4 && device && (
        <StepConfigure configUrl={config.config_url} onRestart={reset} />
      )}
    </div>
  )
}
