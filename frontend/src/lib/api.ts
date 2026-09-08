export type Device = {
  id: string
  name: string
  env: string
  arch: string
  /** 'png' = Farbdisplay via device-ui, 'xbm' = OLED/E-Ink via OEM-Image */
  splash: 'png' | 'xbm' | null
  display: string
  width: number
  height: number
  splash_font: string
  splash_max_px: number
  supported: boolean
  support_level: number
  /** "release" | "pr" | "extra" - alles ausser release ist ungetestet */
  board_level: string
  tags: string[]
  /** Dateiname unter /img/devices/, leer wenn keine Grafik existiert */
  image: string
  image_count: number
  hw_model: number
  /** Klartext-Hinweis zur Variante, leer wenn es nichts zu erklären gibt */
  note: string
  notes: string
  has_display: boolean
}

export type Part = {
  name: string
  offset: number | null
  size: number
  role: string
}

export type Manifest = {
  cache_key: string
  device: string
  device_name: string
  chip: string
  /** 'png' = Splash im Dateisystem, 'xbm' = in die App einkompiliert */
  splash: 'png' | 'xbm' | null
  firmware_ref: string
  erase_all: boolean
  parts: Part[]
  built_at: number
}

export type Build = {
  id: string
  device: string
  firmware_ref: string
  status: 'queued' | 'running' | 'done' | 'error'
  error: string | null
  personalized: boolean
  cache_key: string
  elapsed: number
  manifest: Manifest | null
}

export type SiteConfig = {
  config_url: string
  splash_prefix: string
  /** Vorgabe aus der ENV, z. B. "2.7" oder ein exakter Tag */
  firmware_ref_spec: string
  /** Die daraus aufgelöste Version */
  firmware_ref: string
  admin_enabled: boolean
  /** Zeichenbreiten je Schriftart, aus den Firmware-Fonts extrahiert */
  fonts: Record<string, Record<string, number>>
}

/** Breite eines Textes in Pixeln, so wie die Firmware ihn zeichnen würde. */
export function textWidth(
  text: string,
  table: Record<string, number> | undefined,
): number {
  if (!table) return 0
  const fallback = table[String('n'.charCodeAt(0))] ?? 6
  let sum = 0
  for (const ch of text) sum += table[String(ch.codePointAt(0))] ?? fallback
  return sum
}

/**
 * Hersteller, die Meshtastic aktiv unterstützen — Liste aus dem offiziellen
 * Flasher (types/resources.ts:supportedVendorDeviceTags). Ihre Geräte stehen
 * dort vor den übrigen.
 */
export const VENDOR_TAGS = [
  'RAK', 'B&Q', 'LilyGo', 'Seeed', 'Heltec', 'DIY',
  'Elecrow', 'M5Stack', 'NomadStar', 'muzi',
]

export function isVendorDevice(device: Device): boolean {
  return device.tags.some((tag) => VENDOR_TAGS.includes(tag))
}

export type CatalogAlias = {
  id: string
  name: string
  target: string
  image: string
  note: string
}

export type CatalogSettings = {
  disabled: string[]
  aliases: CatalogAlias[]
}

/**
 * Bildquelle eines Boards. Hochgeladene Bilder tragen das Präfix "upload:"
 * und kommen aus dem Datenverzeichnis, alles andere aus den mitgelieferten
 * Grafiken.
 */
export function deviceImageUrl(image: string): string {
  if (image.startsWith('upload:')) {
    return `/api/catalog/image/${encodeURIComponent(image.slice(7))}`
  }
  return `/img/devices/${image || 'unknown-new.svg'}`
}

export type VersionTag = {
  ref: string
  version: string
  series: string
}

export type VersionSeries = {
  series: string
  latest: VersionTag
  tags: VersionTag[]
}

export type Versions = {
  default: string
  default_spec: string
  series: VersionSeries[]
}

let token: string | null = sessionStorage.getItem('mh_admin_token')

export const auth = {
  get token() {
    return token
  },
  set(value: string | null) {
    token = value
    if (value) sessionStorage.setItem('mh_admin_token', value)
    else sessionStorage.removeItem('mh_admin_token')
  },
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(path, { ...init, headers })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* Antwort war kein JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  config: () => request<SiteConfig>('/api/config'),
  versions: () => request<Versions>('/api/versions'),
  devices: (firmwareRef?: string) =>
    request<{ firmware_ref: string; devices: Device[] }>(
      `/api/devices${firmwareRef ? `?firmware_ref=${encodeURIComponent(firmwareRef)}` : ''}`,
    ),
  startBuild: (
    device: string,
    name?: string,
    overrides?: Record<string, string>,
    firmwareRef?: string,
  ) =>
    request<Build>('/api/build', {
      method: 'POST',
      body: JSON.stringify({
        device,
        name: name || null,
        overrides: overrides || null,
        firmware_ref: firmwareRef || null,
      }),
    }),
  build: (id: string) => request<Build>(`/api/build/${id}`),
  login: (password: string) =>
    request<{ token: string }>('/api/admin/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  adminCatalog: (firmwareRef?: string) =>
    request<{
      firmware_ref: string
      settings: CatalogSettings
      devices: { id: string; name: string; image: string; display: string; arch: string }[]
    }>(`/api/admin/catalog${firmwareRef ? `?firmware_ref=${encodeURIComponent(firmwareRef)}` : ''}`),
  saveCatalog: (settings: CatalogSettings) =>
    request<CatalogSettings>('/api/admin/catalog', {
      method: 'PUT',
      body: JSON.stringify(settings),
    }),
  uploadCatalogImage: async (file: File) => {
    const body = new FormData()
    body.append('file', file)
    const headers = new Headers()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const res = await fetch('/api/admin/catalog/image', { method: 'POST', headers, body })
    if (!res.ok) {
      let detail = res.statusText
      try {
        detail = (await res.json()).detail ?? detail
      } catch {
        /* Antwort war kein JSON */
      }
      throw new Error(detail)
    }
    return (await res.json()) as { image: string }
  },
  exportCatalog: () => request<Record<string, unknown>>('/api/admin/catalog/export'),
  importCatalog: (payload: unknown) =>
    request<CatalogSettings>('/api/admin/catalog/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  adminSchema: () =>
    request<{ overrides: OverrideSpec[]; site: Record<string, string> }>('/api/admin/schema'),
  artifactUrl: (cacheKey: string, name: string) => `/api/artifact/${cacheKey}/${name}`,
}

export type OverrideSpec = {
  key: string
  pref: string | null
  label: string
  type: 'int' | 'str' | 'enum'
  options?: string[]
  min?: number
  max?: number
  placeholder?: string
  help?: string
}

/** SSE-Log-Stream eines Builds. Gibt eine Abbruch-Funktion zurück. */
export function streamLogs(
  buildId: string,
  onLine: (line: string) => void,
  onDone: (build: Build) => void,
  onError: (message: string) => void,
): () => void {
  const source = new EventSource(`/api/build/${buildId}/logs`)
  source.onmessage = (event) => onLine(JSON.parse(event.data))
  source.addEventListener('done', (event) => {
    onDone(JSON.parse((event as MessageEvent).data))
    source.close()
  })
  source.onerror = () => {
    // Der Stream endet auch regulär mit einem Fehler-Event, sobald der
    // Server schließt - deshalb erst prüfen, ob wirklich etwas kaputt ist.
    if (source.readyState === EventSource.CLOSED) return
    onError('Verbindung zum Build-Log abgebrochen')
    source.close()
  }
  return () => source.close()
}
