// Funki's lines for the wizard: one per step, fixed texts (no model). Funny, but every sentence is correct.

export type StepId = 'where' | 'mailbox' | 'password' | 'test' | 'claude' | 'passkey' | 'done'

export const STEPS: StepId[] = ['where', 'mailbox', 'password', 'test', 'claude', 'passkey', 'done']

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
    'Gleich fragt dein Mac vielleicht nach seinem Anmelde-Passwort, weil etwas im Schlüsselbund abgelegt wird. Das ist normal. Wähle „Immer erlauben“, sonst fragt er später bei jeder Mail noch einmal.',
    'Mit dem Auge neben dem Feld siehst du, was du getippt hast. Praktisch bei langen App-Passwörtern; schau nur, dass dir keiner über die Schulter schaut.',
  ],
  test: [
    'Eine Testmail an dich selbst. Kommt sie an, stimmt alles. Kommt sie nicht an, steht hier, woran es lag.',
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
  done: [
    'Geschafft! Schreib Claude zum Beispiel: „Schick mir eine Testmail.“ Ich schaue zu und schweige. Fast.',
    'Diese App brauchst du nur noch zum Einrichten und Verwalten. Geschrieben wird in Claude Desktop.',
  ],
}
