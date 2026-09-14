/**
 * Rechenaufgabe (Proof of Work) für neue Builds.
 *
 * Der Server gibt einen Token und eine Schwierigkeit aus; gesucht ist ein
 * nonce, sodass sha256(token + nonce) mit so vielen Nullbits beginnt. Prüfen
 * kostet den Server Mikrosekunden, Suchen kostet den Browser Sekunden — das
 * ist die Asymmetrie, auf der das beruht.
 *
 * SubtleCrypto scheidet aus: es ist asynchron, und eine Million await-Aufrufe
 * dauern ein Vielfaches. Deshalb hier eine synchrone SHA-256-Implementierung,
 * die in Stücken läuft, damit die Oberfläche nicht einfriert.
 */

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
])

const w = new Uint32Array(64)

/** SHA-256 über ASCII-Text, gibt die ersten vier Bytes als führende Nullbits aus. */
function leadingZeroBits(text: string): number {
  const bytes = new Uint8Array(text.length)
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i) & 0xff

  const blockCount = ((bytes.length + 8) >> 6) + 1
  const padded = new Uint8Array(blockCount * 64)
  padded.set(bytes)
  padded[bytes.length] = 0x80
  const bitLength = bytes.length * 8
  padded[padded.length - 4] = (bitLength >>> 24) & 0xff
  padded[padded.length - 3] = (bitLength >>> 16) & 0xff
  padded[padded.length - 2] = (bitLength >>> 8) & 0xff
  padded[padded.length - 1] = bitLength & 0xff

  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a
  let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19

  for (let block = 0; block < blockCount; block++) {
    const offset = block * 64
    for (let i = 0; i < 16; i++) {
      w[i] = (padded[offset + i * 4] << 24) | (padded[offset + i * 4 + 1] << 16) |
             (padded[offset + i * 4 + 2] << 8) | padded[offset + i * 4 + 3]
    }
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15], b = w[i - 2]
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3)
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0
    }

    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7
    for (let i = 0; i < 64; i++) {
      const s1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))
      const ch = (e & f) ^ (~e & g)
      const t1 = (h + s1 + ch + K[i] + w[i]) | 0
      const s0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))
      const maj = (a & b) ^ (a & c) ^ (b & c)
      const t2 = (s0 + maj) | 0
      h = g; g = f; f = e; e = (d + t1) | 0
      d = c; c = b; b = a; a = (t1 + t2) | 0
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0
    h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0
  }

  // Nur der Anfang zählt: mehr als 32 Nullbits verlangen wir nie.
  if (h0 === 0) return 32
  return Math.clz32(h0 >>> 0)
}

export type SolveProgress = { attempts: number; seconds: number }

/** nonce suchen. `onProgress` wird etwa alle 50 ms aufgerufen. */
export async function solveChallenge(
  challenge: string,
  difficulty: number,
  onProgress?: (progress: SolveProgress) => void,
): Promise<string> {
  if (difficulty <= 0) return '0'
  const started = performance.now()
  let nonce = 0

  for (;;) {
    // In Stuecken rechnen und zwischendurch das Fenster atmen lassen,
    // sonst friert die Seite fuer die Dauer der Suche ein.
    const until = nonce + 20000
    for (; nonce < until; nonce++) {
      if (leadingZeroBits(challenge + nonce) >= difficulty) return String(nonce)
    }
    onProgress?.({ attempts: nonce, seconds: (performance.now() - started) / 1000 })
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
}
