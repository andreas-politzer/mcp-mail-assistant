// Mail providers with settings that were checked against the provider's own documentation or several
// independent sources (November 2026). Anything not in this list goes through "Anderer Anbieter".
// Outlook.com is left out on purpose: Microsoft requires modern sign-in (OAuth2) for it, which a plain
// password cannot do.

export type Security = 'starttls' | 'ssl'

export interface Preset {
  id: string
  label: string
  host: string
  port: number
  security: Security
  // Domains of the address that select this provider automatically.
  domains: string[]
  // What the person has to know about the password before the step with the password.
  passwordNote: string
}

const APP_PW = (what: string) =>
  `${what} nimmt hier nicht dein normales Passwort, sondern ein App-Passwort, das du in deinem Konto selbst erzeugst und jederzeit wieder löschen kannst.`

export const PRESETS: Preset[] = [
  {
    id: 'infomaniak',
    label: 'Infomaniak',
    host: 'mail.infomaniak.com',
    port: 587,
    security: 'starttls',
    domains: ['ik.me'],
    passwordNote:
      'Benutzername ist die vollständige Adresse. Infomaniak will ein eigenes Geräte-Passwort für die Adresse: In der Infomaniak-Mail-Web-App (⋮ neben der Adresse) → Adresse-Einstellungen → Passwort verwalten → Gerät hinzufügen → Reiter IMAP/SMTP → Passwort erzeugen. Es wird nur einmal angezeigt.',
  },
  {
    id: 'gmail',
    label: 'Gmail / Google',
    host: 'smtp.gmail.com',
    port: 587,
    security: 'starttls',
    domains: ['gmail.com', 'googlemail.com'],
    passwordNote:
      'Google nimmt hier nicht dein normales Passwort, sondern ein App-Passwort. Das legst du in deinem Google-Konto unter „Sicherheit“ an; dafür muss die Bestätigung in zwei Schritten eingeschaltet sein.',
  },
  {
    id: 'icloud',
    label: 'Apple iCloud',
    host: 'smtp.mail.me.com',
    port: 587,
    security: 'starttls',
    domains: ['icloud.com', 'me.com', 'mac.com'],
    passwordNote:
      'Benutzername ist die vollständige Adresse. Apple verlangt ein App-spezifisches Passwort: Auf account.apple.com unter „Anmeldung und Sicherheit“ → „App-spezifische Passwörter“ erzeugen.',
  },
  {
    id: 'gmx',
    label: 'GMX',
    host: 'mail.gmx.net',
    port: 587,
    security: 'starttls',
    domains: ['gmx.de', 'gmx.net', 'gmx.at', 'gmx.ch'],
    passwordNote:
      'Bei GMX muss der Zugriff für externe Programme (POP3/IMAP/SMTP) in den Postfach-Einstellungen eingeschaltet sein. Ist die Zwei-Faktor-Anmeldung aktiv, brauchst du dafür ein eigenes Anwendungspasswort aus den GMX-Einstellungen.',
  },
  {
    id: 'webde',
    label: 'WEB.DE',
    host: 'smtp.web.de',
    port: 587,
    security: 'starttls',
    domains: ['web.de'],
    passwordNote:
      'Bei WEB.DE muss der Zugriff für externe Programme (POP3/IMAP/SMTP) in den Einstellungen deines Postfachs eingeschaltet sein, sonst lehnt der Server ab.',
  },
  {
    id: 'tonline',
    label: 'T-Online / Telekom',
    host: 'securesmtp.t-online.de',
    port: 465,
    security: 'ssl',
    domains: ['t-online.de', 'magenta.de'],
    passwordNote:
      'Die Telekom verlangt ein eigenes „Passwort für E-Mail-Programme“. Das ist nicht dein Login-Passwort: Du legst es im Kundencenter bzw. in den E-Mail-Einstellungen unter „Passwort für E-Mail-Programme“ an.',
  },
  {
    id: 'mailboxorg',
    label: 'mailbox.org',
    host: 'smtp.mailbox.org',
    port: 465,
    security: 'ssl',
    domains: ['mailbox.org'],
    passwordNote:
      'Benutzername ist die vollständige Adresse. Mit eingeschalteter Zwei-Faktor-Anmeldung brauchst du ein Mail-App-Passwort (Einstellungen → Sicherheit → Mail-App-Passwörter, mit Haken bei SMTP).',
  },
  {
    id: 'posteo',
    label: 'Posteo',
    host: 'posteo.de',
    port: 587,
    security: 'starttls',
    domains: ['posteo.de', 'posteo.net'],
    passwordNote: 'Benutzername ist die vollständige Adresse, Passwort ist dein Posteo-Passwort.',
  },
  {
    id: 'ionos',
    label: 'IONOS / 1&1',
    host: 'smtp.ionos.de',
    port: 465,
    security: 'ssl',
    domains: [],
    passwordNote: 'Benutzername ist die vollständige Adresse, Passwort ist das Passwort des Postfachs. Falls Port 465 bei dir gesperrt ist, nimm „Anderer Anbieter“ mit Port 587 und STARTTLS.',
  },
  {
    id: 'strato',
    label: 'STRATO',
    host: 'smtp.strato.de',
    port: 465,
    security: 'ssl',
    domains: [],
    passwordNote: 'Benutzername ist die vollständige Adresse, Passwort ist das E-Mail-Passwort des Postfachs.',
  },
  {
    id: 'fastmail',
    label: 'Fastmail',
    host: 'smtp.fastmail.com',
    port: 465,
    security: 'ssl',
    domains: ['fastmail.com', 'fastmail.fm'],
    passwordNote: APP_PW('Fastmail') + ' Du findest es unter Einstellungen → Privatsphäre & Sicherheit → App-Passwörter.',
  },
  {
    id: 'yahoo',
    label: 'Yahoo',
    host: 'smtp.mail.yahoo.com',
    port: 465,
    security: 'ssl',
    domains: ['yahoo.com', 'yahoo.de', 'ymail.com'],
    passwordNote: APP_PW('Yahoo') + ' Du erzeugst es auf der Yahoo-Kontosicherheit-Seite unter „App-Passwort erstellen“.',
  },
  {
    id: 'other',
    label: 'Anderer Anbieter',
    host: '',
    port: 587,
    security: 'starttls',
    domains: [],
    passwordNote:
      'Die Angaben findest du in der Hilfe deines Anbieters unter „SMTP“ oder „Postausgang“. Fast immer sind es Port 587 mit STARTTLS, oder Port 465 mit SSL.',
  },
]

// Addresses that cannot work with a plain password. Shown instead of letting the person fail at the login.
const OUTLOOK = ['outlook.com', 'outlook.de', 'hotmail.com', 'hotmail.de', 'live.com', 'live.de', 'msn.com']
const PROTON = ['proton.me', 'protonmail.com', 'protonmail.ch', 'pm.me']

export function unsupportedReason(email: string): string | null {
  const domain = email.split('@')[1]?.trim().toLowerCase()
  if (!domain) return null
  if (OUTLOOK.includes(domain)) {
    return 'Microsoft lässt für Outlook, Hotmail und Live keine Anmeldung mit Passwort mehr zu, nur noch das moderne Anmeldeverfahren (OAuth2). Das kann Mail Assistant noch nicht. Nimm bitte eine Adresse bei einem anderen Anbieter.'
  }
  if (PROTON.includes(domain)) {
    return 'Proton Mail erlaubt fremden Programmen den Versand nur über die „Proton Mail Bridge“, ein kleines Programm auf deinem Computer mit eigenem Zertifikat. Das unterstützt Mail Assistant noch nicht. Nimm vorerst eine andere Adresse.'
  }
  return null
}

export function presetFor(id: string): Preset {
  return PRESETS.find((p) => p.id === id) ?? PRESETS[PRESETS.length - 1]
}

export function guessPreset(email: string): string | null {
  const domain = email.split('@')[1]?.trim().toLowerCase()
  if (!domain) return null
  return PRESETS.find((p) => p.domains.includes(domain))?.id ?? null
}

export function looksLikeAddress(v: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.trim())
}
