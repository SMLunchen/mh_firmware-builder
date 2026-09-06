import type { Device } from '../lib/api'

type Props = {
  device: Device
  splashPrefix: string
  name: string
  onName: (value: string) => void
  onBack: () => void
  onNext: () => void
}

export default function StepPersonalize({
  device, splashPrefix, name, onName, onBack, onNext,
}: Props) {
  const personalized = name.trim().length > 0
  const preview = personalized ? `${splashPrefix} - ${name.trim()}` : splashPrefix

  return (
    <div className="card">
      <h2>Personalisierter Startbildschirm?</h2>
      <p className="lead">
        Das Display ({device.display}) zeigt beim Start das MeshHessen-Logo.
        Auf Wunsch steht dein Name darunter.
      </p>

      <div className="field">
        <label htmlFor="username">Dein Name (leer lassen für die Standard-Firmware)</label>
        <input
          id="username"
          type="text"
          maxLength={24}
          value={name}
          placeholder="z. B. Manuel"
          onChange={(event) => onName(event.target.value)}
        />
        <div className="help">
          Startbildschirm zeigt: <strong>{preview}</strong>
        </div>
      </div>

      <div className={`notice ${personalized ? 'warn' : 'info'}`}>
        {personalized
          ? 'Personalisierte Firmware muss frisch gebaut werden — das dauert typisch 5–15 Minuten.'
          : 'Die Standard-Firmware liegt meist fertig im Cache und steht sofort bereit.'}
      </div>

      <div className="row">
        <button className="ghost" onClick={onBack}>Zurück</button>
        <div className="spacer" />
        <button onClick={onNext}>
          {personalized ? 'Firmware bauen' : 'Weiter'}
        </button>
      </div>
    </div>
  )
}
