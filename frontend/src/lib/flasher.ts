import { ESPLoader, Transport } from 'esptool-js'
import type { Manifest } from './api'
import { api } from './api'

/**
 * Wir flashen bewusst die Einzelteile an ihren echten Partition-Offsets statt
 * einer factory.bin: die factory.bin endet bei ~3,7 MB und enthält das
 * LittleFS-Image (0xc90000) gar nicht. Wer nur sie flasht, hat danach ein
 * leeres Dateisystem und damit keinen Splash.
 */

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

export function serialSupported(): boolean {
  return typeof navigator !== 'undefined' && 'serial' in navigator
}

export async function flash(
  manifest: Manifest,
  onLog: (line: string) => void,
  onProgress: (progress: FlashProgress) => void,
): Promise<void> {
  if (!serialSupported()) {
    throw new Error(
      'Dieser Browser kann nicht flashen. Nötig ist Chrome oder Edge über HTTPS.',
    )
  }

  const flashable = manifest.parts.filter((p) => p.offset !== null)
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
    const chip = await loader.main()
    onLog(`Verbunden: ${chip}`)

    onLog('Lösche Flash vollständig ...')
    onProgress({ phase: 'Flash löschen', fileIndex: 0, fileCount: flashable.length, percent: 0 })

    await loader.writeFlash({
      fileArray,
      flashSize: 'keep',
      flashMode: 'keep',
      flashFreq: 'keep',
      eraseAll: manifest.erase_all,
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
    await loader.after()
    onLog('Gerät wird neu gestartet.')
  } finally {
    try {
      await transport.disconnect()
    } catch {
      /* Port war schon zu */
    }
  }
}
