import { useEffect } from 'react'

/**
 * Animiertes Knotennetz im Hintergrund — dieselbe Darstellung wie in der
 * MeshHessen-Schnellkonfiguration, damit beide Anwendungen zusammengehören.
 * Das Skript liegt als klassisches <script> in public/, nicht als Modul.
 */
export default function ParticleBackground() {
  useEffect(() => {
    const script = document.createElement('script')
    script.src = '/particles.js'
    script.async = true
    document.body.appendChild(script)
    return () => {
      script.remove()
    }
  }, [])

  return (
    <div className="bg-canvas" aria-hidden="true">
      <canvas id="particles" />
    </div>
  )
}
