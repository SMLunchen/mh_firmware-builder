type Props = {
  configUrl: string
  onRestart: () => void
}

export default function StepConfigure({ configUrl, onRestart }: Props) {
  return (
    <div className="card">
      <h2>Node jetzt konfigurieren?</h2>
      <p className="lead">
        Die Firmware ist drauf. In der Schnellkonfiguration setzt du Name, Standort
        und alle MeshHessen-Parameter in einem Durchgang.
      </p>

      <div className="choice">
        <button onClick={() => window.open(configUrl, '_blank', 'noopener')}>
          Ja, Node konfigurieren
        </button>
        <button className="ghost" onClick={onRestart}>
          Nein, weiteres Gerät flashen
        </button>
      </div>

      <div className="help" style={{ color: 'var(--muted)', fontSize: 13, marginTop: 14 }}>
        Die Konfiguration öffnet sich in einem neuen Tab. Lass das Gerät dafür
        angeschlossen — sie verbindet sich per USB, Bluetooth oder WLAN.
      </div>
    </div>
  )
}
