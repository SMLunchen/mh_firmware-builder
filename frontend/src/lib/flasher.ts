import { ESPLoader, Transport } from 'esptool-js'
import type { Manifest, Part } from './api'
import { api } from './api'

/**
 * Wir flashen bewusst die Einzelteile an ihren echten Partition-Offsets statt
 * einer factory.bin: die factory.bin endet bei ~3,7 MB und enthält das
 * LittleFS-Image (0xc90000) gar nicht. Wer nur sie flasht, hat danach ein
 * leeres Dateisystem und damit keinen Splash.
 */

/**
 * update     - nur die App-Partition, ohne Löschen. Konfiguration, Schlüssel
 *              und Dateisystem bleiben erhalten.
 * update_fs  - App und Dateisystem, ohne Löschen. Erneuert zusätzlich den
 *              Splash von Farbdisplays, behält aber die Konfiguration.
 * full       - alles löschen und neu schreiben. Setzt das Gerät zurück.
 */
export type FlashMode = 'update' | 'update_fs' | 'full'

export function partsForMode(manifest: Manifest, mode: FlashMode): Part[] {
  const flashable = manifest.parts.filter((p) => p.offset !== null)
  if (mode === 'full') return flashable
  const roles = mode === 'update' ? ['app'] : ['app', 'filesystem']
  return flashable.filter((p) => roles.includes(p.role))
}

export function hasFilesystem(manifest: Manifest): boolean {
  return manifest.parts.some((p) => p.role === 'filesystem' && p.offset !== null)
}

export type FlashProgress = {
  phase: string
  fileIndex: number
  fileCount: number
  percent: number
}

function toBinaryString(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let out = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    out += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return out
}

/**
 * 1200-Baud-Reset ("touch"): Der Port wird kurz mit 1200 Baud geoeffnet und
 * wieder geschlossen. ESP32-S3-Boards mit nativem USB werten das als Signal,
 * in den Download-Modus zu starten. Ohne das bekommt man z. B. das T-Deck oft
 * nicht in den Bootloader, ohne die BOOT-Taste zu halten.
 */
/**
 * Zuletzt von uns geoeffneter Port. Web Serial liefert fuer dasselbe Geraet
 * dasselbe Port-Objekt zurueck - bleibt es offen, scheitert jeder weitere
 * open() mit "Failed to open serial port", bis die Seite neu geladen wird.
 */
let openedPort: SerialPort | null = null

async function releaseOpenPort(onLog?: (line: string) => void): Promise<void> {
  if (!openedPort) return
  try {
    await openedPort.close()
    onLog?.('Zuvor geöffneten Port geschlossen.')
  } catch {
    /* war bereits zu */
  }
  openedPort = null
}

export async function baud1200Reset(onLog: (line: string) => void): Promise<void> {
  if (!serialSupported()) {
    throw new Error('Dieser Browser unterstützt Web Serial nicht.')
  }
  await releaseOpenPort(onLog)
  const port = await navigator.serial.requestPort()
  onLog('Öffne Port mit 1200 Baud ...')
  try {
    await port.open({ baudRate: 1200 })
    openedPort = port
    // Dem Geraet einen Moment geben, die 1200-Baud-Verbindung zu erkennen
    await new Promise((resolve) => setTimeout(resolve, 500))
  } finally {
    // Muss in jedem Fall passieren - ein offen gebliebener Port blockiert
    // sonst das anschliessende Flashen.
    try {
      await port.close()
    } catch {
      /* schon zu */
    }
    openedPort = null
  }
  onLog('✓ Port wieder geschlossen — das Gerät sollte jetzt im Download-Modus sein.')
}

export function serialSupported(): boolean {
  return typeof navigator !== 'undefined' && 'serial' in navigator
}

export async function flash(
  manifest: Manifest,
  mode: FlashMode,
  onLog: (line: string) => void,
  onProgress: (progress: FlashProgress) => void,
): Promise<void> {
  if (!serialSupported()) {
    throw new Error(
      'Dieser Browser kann nicht flashen. Nötig ist Chrome oder Edge über HTTPS.',
    )
  }

  const flashable = partsForMode(manifest, mode)
  if (flashable.length === 0) {
    throw new Error(
      `Für ${manifest.device_name} gibt es kein serielles Flash-Image. ` +
        'Lade die Datei herunter und spiele sie per DFU/UF2 auf.',
    )
  }

  onLog('Lade Firmware-Dateien ...')
  onProgress({ phase: 'Dateien laden', fileIndex: 0, fileCount: flashable.length, percent: 0 })

  const fileArray = []
  for (const part of flashable) {
    const res = await fetch(api.artifactUrl(manifest.cache_key, part.name))
    if (!res.ok) throw new Error(`${part.name} konnte nicht geladen werden`)
    fileArray.push({
      data: toBinaryString(await res.arrayBuffer()),
      address: part.offset as number,
    })
    onLog(`  ${part.name} -> 0x${(part.offset as number).toString(16)} (${part.role})`)
  }

  await releaseOpenPort(onLog)
  const port = await navigator.serial.requestPort()
  const transport = new Transport(port, true)

  const terminal = {
    clean: () => {},
    writeLine: (data: string) => onLog(data),
    write: (data: string) => onLog(data),
  }

  const loader = new ESPLoader({ transport, baudrate: 460800, romBaudrate: 115200, terminal })

  try {
    onProgress({ phase: 'Verbinden', fileIndex: 0, fileCount: flashable.length, percent: 0 })
    let chip: string
    try {
      chip = await loader.main()
    } catch (err) {
      const message = (err as Error).message
      if (/failed to open/i.test(message)) {
        throw new Error(
          'Der serielle Port lässt sich nicht öffnen. Meist hält ihn noch etwas ' +
            'anderes: ein zweiter Tab, ein Serial-Monitor oder ein Terminal. ' +
            'Schließe das und lade diese Seite neu.',
        )
      }
      throw err
    }
    onLog(`Verbunden: ${chip}`)

    if (mode === 'full') {
      onLog('Lösche Flash vollständig ...')
      onProgress({ phase: 'Flash löschen', fileIndex: 0, fileCount: flashable.length, percent: 0 })
    } else {
      onLog('Schreibe ohne Vollerase — Konfiguration und Schlüssel bleiben erhalten.')
    }

    await loader.writeFlash({
      fileArray,
      flashSize: 'keep',
      flashMode: 'keep',
      flashFreq: 'keep',
      eraseAll: mode === 'full',
      compress: true,
      reportProgress: (fileIndex: number, written: number, total: number) => {
        onProgress({
          phase: `Schreibe ${flashable[fileIndex]?.name ?? ''}`,
          fileIndex,
          fileCount: flashable.length,
          percent: total > 0 ? Math.round((written / total) * 100) : 0,
        })
      },
    })

    onLog('✓ Alle Partitionen geschrieben')

    // Neustart ueber die RTS-Leitung: RTS=true zieht EN auf LOW (Chip im
    // Reset), RTS=false gibt ihn wieder frei und der Chip bootet. Das ist die
    // Sequenz, die auch der offizielle Web-Flasher verwendet.
    onLog('Starte Gerät neu (RTS) ...')
    onProgress({ phase: 'Neustart', fileIndex: flashable.length - 1,
                 fileCount: flashable.length, percent: 100 })
    await transport.setRTS(true)
    await new Promise((resolve) => setTimeout(resolve, 100))
    await transport.setRTS(false)
    onLog('✓ Gerät startet neu.')
  } finally {
    try {
      await transport.disconnect()
    } catch {
      /* Port war schon zu */
    }
  }
}
