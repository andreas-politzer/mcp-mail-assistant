// Funki's lines for the wizard: one per step, fixed texts (no model). Funny, but every sentence is correct.

export type StepId = 'where' | 'mailbox' | 'password' | 'test' | 'claude' | 'passkey' | 'tunnel' | 'phonelogin' | 'phonekey' | 'done'

export const STEPS: StepId[] = ['where', 'mailbox', 'password', 'test', 'claude', 'passkey', 'done']

// Whoever wants to use the phone as well gets the tunnel, the phone sign-in and the phone's passkey before the end.
export const STEPS_PHONE: StepId[] = ['where', 'mailbox', 'password', 'test', 'claude', 'passkey', 'tunnel', 'phonelogin', 'phonekey', 'done']

export const HINTS: Record<StepId, string[]> = {
  where: [
    'Willkommen! Für fast alle passt die erste Antwort: „Nur an diesem Computer“. Sie braucht keine Adresse im Internet und kein zweites Konto.',
    'Vom Handy aus geht später auch. Dafür braucht dein Computer einen Tunnel, also eine Adresse, unter der das Internet ihn findet. Das richten wir dann in Ruhe ein.',
  ],
  mailbox: [
    'Das ist die Adresse, von der aus deine Mails rausgehen. Nicht die deines Chefs. Auch nicht meine, ich bin ein Telefon.',
    'Server und Port füllt der Anbieter-Eintrag aus. Nur bei „Anderer Anbieter“ musst du sie selbst eintragen, die Hilfeseite deines Anbieters kennt sie.',
  ],
  password: [
    'Das Passwort geht direkt in den Schlüsselbund deines Computers. Ich sehe es nicht, und im Fenster bleibt es auch nicht stehen.',
    'Viele Anbieter wollen hier ein App-Passwort statt des normalen. Das ist keine Schikane, sondern ein zweites Passwort, das du jederzeit einzeln wieder löschen kannst.',
    'Mit dem Auge neben dem Feld siehst du, was du getippt hast. Praktisch bei langen App-Passwörtern; schau nur, dass dir keiner über die Schulter schaut.',
  ],
  test: [
    'Eine Testmail an dich selbst. Kommt sie an, stimmt alles. Kommt sie nicht an, steht hier, woran es lag.',
    'Keine Lust auf die Testmail? „Überspringen“ geht. Du kannst sie später im Einrichtungs-Wizard nachholen.',
    'Die Haken bei „eigene Adresse“: Mails an dich darf Claude im Chat freigeben. An alle anderen nur du, mit Passkey. Das ist der ganze Trick.',
  ],
  claude: [
    'Jetzt bekommt Claude Desktop den Hinweis, dass es mich gibt. Ich ändere nur meinen eigenen Eintrag und lege vorher eine Kopie an.',
    'Danach Claude Desktop einmal ganz beenden und neu starten: auf dem Mac mit Cmd+Q, nicht nur das Fenster zumachen. Sonst weiß es noch von nichts.',
  ],
  passkey: [
    'Der Passkey ist dein Daumenabdruck für Mails an andere: Ohne ihn geht keine einzige raus. Bei Mails an dich selbst fragt Claude im Chat nur kurz nach.',
    'Du kannst das auch später nachholen. Bis dahin gehen aber nur Mails an dich selbst.',
  ],
  tunnel: [
    'Der Tunnel gibt deinem Computer eine Adresse im Internet, unter der dein Handy ihn findet. Dafür nehmen wir Tailscale. Dort meldest du dich selbst an, Mail Assistant installiert nichts.',
    'Die Adresse enthält den Namen deines Computers und ist danach öffentlich sichtbar. Ohne deine Passphrase kommt trotzdem niemand hinein.',
    'Beim ersten Mal holt Tailscale ein Zertifikat. Das kann ein bis zwei Minuten dauern. Dann hilft „Erneut prüfen“.',
  ],
  phonelogin: [
    'Die Passphrase ist der Schlüssel für dein Handy. Ich zeige sie dir einmal, danach kenne selbst ich sie nicht mehr. Ab damit in den Passwortmanager.',
    'Verloren? Kein Drama: Du erzeugst einfach eine neue. Die alte ist dann ungültig, und dein Handy meldet sich noch einmal an.',
  ],
  phonekey: [
    'Dein Handy bekommt seinen eigenen Passkey, denn er gehört zur Adresse deines Computers im Internet. Der vom Computer gilt dort nicht.',
    'Mehr als zwei Passkeys gehen nicht. Ist schon ein alter da, entferne ihn zuerst in der Verwaltung.',
  ],
  done: [
    'Geschafft! Schreib Claude zum Beispiel: „Schick mir eine Testmail.“ Ich schaue zu und schweige. Fast.',
    'Diese App brauchst du nur noch zum Einrichten und Verwalten. Geschrieben wird in Claude Desktop.',
  ],
}
