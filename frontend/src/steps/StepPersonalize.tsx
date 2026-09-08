import { textWidth, type Device, type SiteConfig } from '../lib/api'

type Props = {
  device: Device
  config: SiteConfig
  name: string
  onName: (value: string) => void
  onBack: () => void
  onNext: () => void
}

export default function StepPersonalize({
  device, config, name, onName, onBack, onNext,
}: Props) {
  const trimmed = name.trim()
  const personalized = trimmed.length > 0

  // Beim Farbdisplay trägt das Logo den Schriftzug schon im Bild — darunter
  // kommt nur noch der Name. Sonst steht dort "Mesh Hessen - Name".
  const logoHasWordmark = device.splash === 'png'
  const preview = logoHasWordmark
    ? trimmed
    : personalized
      ? `${config.splash_prefix} - ${trimmed}`
      : config.splash_prefix

  const table = config.fonts[device.splash_font]
  const px = textWidth(preview, table)
  const max = device.splash_max_px
  const tooWide = max > 0 && px > max

  return (
    <div className="card">
      <h2>Personalisierter Startbildschirm?</h2>
      <p className="lead">
        Das Display ({device.display}, {device.width}×{device.height}) zeigt beim
        Start das MeshHessen-Logo. Auf Wunsch steht dein Name darunter.
      </p>

      <div className="field">
        <label htmlFor="username">Dein Name (leer lassen für die Standard-Firmware)</label>
        <input
          id="username"
          type="text"
          maxLength={32}
          value={name}
          placeholder="z. B. Manuel"
          onChange={(event) => onName(event.target.value)}
        />
        <div className="help">
          Startbildschirm zeigt:{' '}
          <strong>{preview || '— nur das Logo —'}</strong>
          {max > 0 && (
            <> · {px} von {max} px</>
          )}
        </div>
      </div>

      {tooWide && (
        <div className="notice err">
          Der Text ist {px - max} px zu breit für dieses Display. Die Firmware
          zentriert ihn und schneidet ihn dann an beiden Enden ab — kürze den
          Namen.
        </div>
      )}

      {logoHasWordmark && (
        <div className="notice info">
          Das Logo dieses Geräts enthält den Schriftzug „Mesh Hessen“ bereits.
          Darunter erscheint deshalb nur dein Name, sonst stünde es doppelt.
        </div>
      )}

      {!tooWide && (
        <div className={`notice ${personalized ? 'warn' : 'info'}`}>
          {personalized
            ? 'Personalisierte Firmware muss frisch gebaut werden — das dauert typisch 5–15 Minuten.'
            : 'Die Standard-Firmware liegt meist fertig im Cache und steht sofort bereit.'}
        </div>
      )}

      <div className="row">
        <button className="ghost" onClick={onBack}>Zurück</button>
        <div className="spacer" />
        <button onClick={onNext} disabled={tooWide}>
          {personalized ? 'Firmware bauen' : 'Weiter'}
        </button>
      </div>
    </div>
  )
}
