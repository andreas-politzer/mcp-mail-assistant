import type { ReactNode } from 'react'
import { TUNNEL_COMMAND } from '../api/admin'

// Help and instructions. Short, in plain sentences, and only about what the app does today.

function Part({ title, open, children }: { title: string; open?: boolean; children: ReactNode }) {
  return (
    <details open={open} className="border-b border-white/20 py-2">
      <summary className="cursor-pointer text-[13px] font-semibold">{title}</summary>
      <div className="text-[12px] leading-relaxed mt-1.5 space-y-1.5">{children}</div>
    </details>
  )
}

export function HelpTile() {
  return (
    <div className="tile-text flex-1 min-h-0 flex flex-col">
      <div className="scroll-glass flex-1 min-h-0 px-6 pb-5">
        <Part title="Wofür diese Verwaltung da ist" open>
          <p>Hier legst du fest, was Claude darf. Du bestimmst, an wen Claude Mails vorbereiten darf und welche Dateien Claude anhängen darf.</p>
          <p>Gesendet wird nie hier. Gesendet wird nur im Chat, und nur nachdem du dort ausdrücklich Ja gesagt hast.</p>
          <p>Du brauchst diese Verwaltung selten. Du legst etwas einmal fest und schaust später nur für eine Kleinigkeit wieder hinein.</p>
        </Part>

        <Part title="Verbindung zum Server">
          <p>Die Verwaltung erreicht den Server nur über einen SSH-Tunnel. Öffne dafür ein Terminal und gib diesen Befehl ein. Das Terminal bleibt danach offen und still, das ist richtig.</p>
          <pre className="p-2 rounded text-[11px] overflow-x-auto" style={{ background: 'rgba(0,0,0,0.35)' }}>{TUNNEL_COMMAND}</pre>
          <p>Ist die Verwaltung nicht erreichbar, fehlt fast immer dieser Tunnel.</p>
        </Part>

        <Part title="Empfänger">
          <p>Unter Neu, noch nicht freigegeben stehen Adressen, die Claude im Chat vorgeschlagen hat. Du entscheidest: freigeben, ablehnen oder für immer sperren.</p>
          <p>Unter Freigegeben stehen die Adressen, an die Claude Mails vorbereiten darf. Entfernen nimmt eine Adresse wieder heraus.</p>
          <p>Eine gesperrte Adresse kommt nie wieder, auch nicht über eine Kontaktdatei. Du kannst die Sperre aufheben. Freigegeben ist die Adresse dann noch nicht.</p>
          <p>Mit CSV importieren liest du eine Kontaktdatei ein. Du siehst zuerst eine Vorschau. Danach werden die neuen Adressen nur Kandidaten, nichts wird automatisch freigegeben. Eine Datei darf höchstens 2000 Zeilen haben.</p>
          <p>Kandidaten, über die du nicht entscheidest, verfallen nach 30 Tagen. Abgelehnte Kandidaten kommen nie wieder. Solange Kandidaten offen sind, pausieren Claudes eigene Vorschläge.</p>
        </Part>

        <Part title="Dateien und Anhänge">
          <p>Eine Quelle ist ein Ordner oder eine einzelne Datei auf diesem Mac, oder der Notizordner auf dem Server. Claude sieht nur, was du ausdrücklich freigibst. Eine Sperre gewinnt immer.</p>
          <p>Mit Öffnen siehst du den Baum einer Quelle. Gib einen Ordner frei oder den ganzen Ordner. Einzelne Dateien lassen sich nur sperren.</p>
          <p>Dateien von diesem Mac kommen erst beim Abgleich auf den Server. Der Abgleich zeigt dir vorher, was neu ist, was sich geändert hat und was vom Server gelöscht wird. Erst nach deinem Klick passiert etwas.</p>
          <p>Stimmt etwas nicht, bricht der Abgleich ab und löscht nichts. Das gilt, wenn der Ordner fehlt, ersetzt wurde oder nicht vollständig gelesen werden konnte.</p>
          <p>Es gehen nur Dateien mit der Endung .md oder .txt. Dateien mit Namen, die nach Passwörtern oder Zugangsdaten klingen, werden nie übertragen.</p>
          <p>Wo ein Ordner auf deinem Mac liegt, bleibt auf diesem Mac. Der Server und das Protokoll erfahren es nicht.</p>
        </Part>

        <Part title="Einstellungen">
          <p>Hier legst du fest, wie viele Mails Claude pro Stunde und pro Tag senden darf und wie lange ein Vorschlag offen bleibt. Du siehst auch, wie viel schon verbraucht ist. Die Sicherheitsübersicht zeigt, was geschützt ist. Sie lässt sich nicht ändern.</p>
        </Part>

        <Part title="Aktivität">
          <p>Hier siehst du, was passiert ist: Aktionen wie Freigaben, Sperren und Abgleiche, ohne Mailtexte und ohne Betreffzeilen. Du kannst nach Art und Zeitraum filtern, zum Beispiel nur die letzte Stunde.</p>
        </Part>

        <Part title="Sitzung und Abmelden">
          <p>Nach 20 Minuten ohne Eingabe meldet der Server dich ab, spätestens nach 4 Stunden. Die Passphrase liegt nur im Arbeitsspeicher. Mit Abmelden oder Herunterfahren endet die Sitzung sofort.</p>
        </Part>

        <Part title="Wenn etwas nicht klappt">
          <p>Der Server antwortet nicht: Prüfe, ob der Tunnel noch läuft.</p>
          <p>Eine Quelle ist nicht erreichbar: Der Ordner wurde verschoben oder umbenannt. Wähle in der Liste Neu zuordnen und zeige auf den neuen Ort.</p>
          <p>Ein Abgleich bricht ab: Lies den Text im Fenster. Er nennt den Grund. Es wurde nichts gelöscht.</p>
        </Part>
      </div>
    </div>
  )
}
