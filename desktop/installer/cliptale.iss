; Installer di ClipTale (Inno Setup 6). Non si compila a mano: usa  python desktop/build_installer.py
; che prepara i file in build/installer/stage e passa la versione (AppVersion).
;
; Cosa fa: copia il programma in C:\Program Files\ClipTale (solo codice, leggero), crea i collegamenti nel menu Start
; e sul desktop. Alla prima apertura ClipTale.exe prepara Python e le librerie in %LOCALAPPDATA%\ClipTale; disegni e
; voci si scaricano dall'app. I video dell'utente stanno in Video\ClipTale e non vengono mai cancellati.
; /UPDATE (usato dagli aggiornamenti automatici): a fine installazione riapre ClipTale.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Stage
  #define Stage "..\..\build\installer\stage"
#endif

[Setup]
AppId={{6E3A1C52-8B4F-4C7E-9D21-C11A7A1E0F55}
AppName=ClipTale
AppVersion={#AppVersion}
AppVerName=ClipTale {#AppVersion}
AppPublisher=ClipTale
AppPublisherURL=https://github.com/Vincisus/TiktokFactory
AppSupportURL=https://github.com/Vincisus/TiktokFactory/issues
AppUpdatesURL=https://github.com/Vincisus/TiktokFactory/releases
DefaultDirName={autopf}\ClipTale
DefaultGroupName=ClipTale
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\..\build\installer
OutputBaseFilename=ClipTale-Setup-{#AppVersion}
SetupIconFile=..\cliptale.ico
UninstallDisplayIcon={app}\ClipTale.exe
UninstallDisplayName=ClipTale
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto
CloseApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=ClipTale

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
it.FirstRun=Alla prima apertura ClipTale scarica Python e le sue librerie (circa 300 MB): serve la connessione a Internet.
en.FirstRun=The first time it opens, ClipTale downloads Python and its libraries (about 300 MB): an Internet connection is needed.
es.FirstRun=La primera vez que se abre, ClipTale descarga Python y sus bibliotecas (unos 300 MB): se necesita conexión a Internet.
fr.FirstRun=À la première ouverture, ClipTale télécharge Python et ses bibliothèques (environ 300 Mo) : une connexion Internet est nécessaire.
de.FirstRun=Beim ersten Start lädt ClipTale Python und seine Bibliotheken herunter (etwa 300 MB): Eine Internetverbindung ist nötig.
it.CloseApp=ClipTale è aperto. Chiudilo (menu dell'icona nella barra > Esci da ClipTale) e premi OK.
en.CloseApp=ClipTale is running. Close it (tray icon menu > Exit ClipTale) and press OK.
es.CloseApp=ClipTale está abierto. Ciérralo (menú del icono de la barra > Salir de ClipTale) y pulsa Aceptar.
fr.CloseApp=ClipTale est ouvert. Ferme-le (menu de l'icône de la barre > Quitter ClipTale) puis clique sur OK.
de.CloseApp=ClipTale ist geöffnet. Schließe es (Menü des Taskleistensymbols > ClipTale beenden) und klicke auf OK.
it.RemoveData=Vuoi eliminare anche i componenti scaricati e le impostazioni (Python, modelli di disegno e voci, chiavi)?%n%nI tuoi video in Video\ClipTale non vengono toccati.
en.RemoveData=Do you also want to delete the downloaded components and settings (Python, drawing and voice models, keys)?%n%nYour videos in Videos\ClipTale are not touched.
es.RemoveData=¿Quieres eliminar también los componentes descargados y la configuración (Python, modelos de dibujo y voz, claves)?%n%nTus vídeos en Vídeos\ClipTale no se tocan.
fr.RemoveData=Veux-tu aussi supprimer les composants téléchargés et les réglages (Python, modèles de dessin et de voix, clés) ?%n%nTes vidéos dans Vidéos\ClipTale ne sont pas touchées.
de.RemoveData=Sollen auch die heruntergeladenen Komponenten und Einstellungen gelöscht werden (Python, Zeichen- und Stimmmodelle, Schlüssel)?%n%nDeine Videos in Videos\ClipTale bleiben erhalten.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; un aggiornamento sostituisce tutto il codice: niente file vecchi rimasti in giro
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ClipTale"; Filename: "{app}\ClipTale.exe"; WorkingDir: "{app}"; Comment: "ClipTale"; AppUserModelID: "ClipTale.Desktop"
Name: "{autodesktop}\ClipTale"; Filename: "{app}\ClipTale.exe"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "ClipTale.Desktop"

[Run]
Filename: "{app}\ClipTale.exe"; Description: "{cm:LaunchProgram,ClipTale}"; Flags: nowait postinstall skipifsilent runasoriginaluser
Filename: "{app}\ClipTale.exe"; Flags: nowait runasoriginaluser; Check: IsUpdate

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"

[Code]
function IsUpdate: Boolean;
begin
  Result := Pos('/UPDATE', UpperCase(GetCmdTail)) > 0;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpReady then
    WizardForm.ReadyMemo.Lines.Add(#13#10 + CustomMessage('FirstRun'));
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  while CheckForMutexes('ClipTaleRunning') do
    if MsgBox(CustomMessage('CloseApp'), mbInformation, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\ClipTale');
    // in modalità silenziosa i dati restano; altrimenti si chiede (risposta predefinita: no)
    if (not UninstallSilent) and DirExists(Data) and (MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
      DelTree(Data, True, True, True);
  end;
end;
