import { t, useLang } from '../i18n'
import type { ReactNode } from 'react'
// Help and instructions. Short, in plain sentences, and only about what the app does today.
// The tunnel command here is always the generic form. A person's own server address never appears in the help.
import { GENERIC_TUNNEL_COMMAND } from '../api/admin'

function Part({ title, open, children }: { title: string; open?: boolean; children: ReactNode }) {
  return (
    <details open={open} className="border-b border-white/20 py-2">
      <summary className="cursor-pointer text-[13px] font-semibold">{title}</summary>
      <div className="text-[12px] leading-relaxed mt-1.5 space-y-1.5">{children}</div>
    </details>
  )
}

export function HelpTile() {
  useLang()
  return (
    <div className="tile-text flex-1 min-h-0 flex flex-col">
      <div className="scroll-glass flex-1 min-h-0 px-6 pb-5">
        <Part title={t('Wofür diese Verwaltung da ist')} open>
          <p>{t('Hier legst du fest, was Claude darf. Du bestimmst, an wen Claude Mails vorbereiten darf und welche Dateien Claude anhängen darf.')}</p>
          <p>{t('Gesendet wird nie hier. Gesendet wird nur im Chat, und nur nachdem du dort ausdrücklich Ja gesagt hast.')}</p>
          <p>{t('Du brauchst diese Verwaltung selten. Du legst etwas einmal fest und schaust später nur für eine Kleinigkeit wieder hinein.')}</p>
        </Part>

        <Part title={t('Verbindung zum Server')}>
          <p>{t('Die Verwaltung erreicht den Server nur über einen SSH-Tunnel. Öffne dafür ein Terminal und gib diesen Befehl ein. Ersetze BENUTZER durch deinen Benutzernamen auf dem Server und SERVER durch dessen Adresse. Das Terminal bleibt danach offen und still, das ist richtig.')}</p>
          <pre className="p-2 rounded text-[11px] overflow-x-auto" style={{ background: 'rgba(0,0,0,0.35)' }}>{GENERIC_TUNNEL_COMMAND}</pre>
          <p>{t('Ist die Verwaltung nicht erreichbar, fehlt fast immer dieser Tunnel.')}</p>
        </Part>

        <Part title={t('Empfänger')}>
          <p>{t('Unter Neu, noch nicht freigegeben stehen Adressen, die Claude im Chat vorgeschlagen hat. Du entscheidest: freigeben, ablehnen oder für immer sperren.')}</p>
          <p>{t('Unter Freigegeben stehen die Adressen, an die Claude Mails vorbereiten darf. Entfernen nimmt eine Adresse wieder heraus.')}</p>
          <p>{t('Eine gesperrte Adresse kommt nie wieder, auch nicht über eine Kontaktdatei. Du kannst die Sperre aufheben. Freigegeben ist die Adresse dann noch nicht.')}</p>
          <p>{t('Mit CSV importieren liest du eine Kontaktdatei ein. Du siehst zuerst eine Vorschau. Danach werden die neuen Adressen nur Kandidaten, nichts wird automatisch freigegeben. Eine Datei darf höchstens 2000 Zeilen haben.')}</p>
          <p>{t('Kandidaten, über die du nicht entscheidest, verfallen nach einer Frist. Die Frist stellst du unter Einstellungen ein, die Vorgabe sind 30 Tage. Abgelehnte Kandidaten kommen nie wieder. Sind zu viele Vorschläge offen, macht Claude keine weiteren. Die Vorgabe sind fünf.')}</p>
        </Part>

        <Part title={t('Dateien und Anhänge')}>
          <p>{t('Eine Quelle ist ein Ordner oder eine einzelne Datei auf diesem Computer, oder der Notizordner auf dem Server. Claude sieht nur, was du ausdrücklich freigibst. Eine Sperre gewinnt immer.')}</p>
          <p>{t('Mit Öffnen siehst du den Baum einer Quelle. Gib einen Ordner frei oder den ganzen Ordner. Einzelne Dateien lassen sich nur sperren.')}</p>
          <p>{t('Dateien von diesem Computer kommen erst beim Abgleich auf den Server. Der Abgleich zeigt dir vorher, was neu ist, was sich geändert hat und was vom Server gelöscht wird. Erst nach deinem Klick passiert etwas.')}</p>
          <p>{t('Stimmt etwas nicht, bricht der Abgleich ab und löscht nichts. Das gilt, wenn der Ordner fehlt, ersetzt wurde oder nicht vollständig gelesen werden konnte.')}</p>
          <p>{t('Es gehen nur Dateien mit der Endung .md oder .txt. Versteckte Dateien und Dateien mit Namen, die nach Passwörtern oder Zugangsdaten klingen, werden nie übertragen.')}</p>
          <p>{t('Wo ein Ordner auf deinem Computer liegt, bleibt auf diesem Computer. Der Server und das Protokoll erfahren es nicht.')}</p>
        </Part>

        <Part title={t('Einstellungen')}>
          <p>{t('Hier legst du fest, wie viele Mails Claude pro Stunde und pro Tag senden darf und wie lange ein Vorschlag offen bleibt. Du siehst auch, wie viel schon verbraucht ist. Die Sicherheitsübersicht zeigt, was geschützt ist. Sie lässt sich nicht ändern.')}</p>
        </Part>

        <Part title={t('Aktivität')}>
          <p>{t('Hier siehst du, was passiert ist: Aktionen wie Freigaben, Sperren und Abgleiche, ohne Mailtexte und ohne Betreffzeilen. Du kannst nach Art und Zeitraum filtern, zum Beispiel nur die letzte Stunde.')}</p>
        </Part>

        <Part title={t('Sitzung und Abmelden')}>
          <p>{t('Der Server meldet dich ab, wenn 20 Minuten lang nichts passiert, und spätestens nach 4 Stunden. Das sind die Vorgaben des Servers. Die Passphrase liegt nur im Arbeitsspeicher. Mit Herunterfahren im Startmenü endet die Sitzung sofort, und das Fenster schließt sich.')}</p>
        </Part>

        <Part title={t('Wenn etwas nicht klappt')}>
          <p>{t('Der Server antwortet nicht: Prüfe, ob der Tunnel noch läuft.')}</p>
          <p>{t('Eine Quelle ist nicht erreichbar: Der Ordner wurde verschoben oder umbenannt. Wähle in der Liste Neu zuordnen und zeige auf den neuen Ort.')}</p>
          <p>{t('Ein Abgleich bricht ab: Lies den Text im Fenster. Er nennt den Grund. Es wurde nichts gelöscht.')}</p>
        </Part>
      </div>
    </div>
  )
}
