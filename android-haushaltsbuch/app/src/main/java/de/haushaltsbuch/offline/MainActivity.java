package de.haushaltsbuch.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PrintManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.print.PrintAttributes;
import android.print.PrintDocumentAdapter;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private static final int REQUEST_OPEN_APP_HTML = 1000;
    private static final int REQUEST_OPEN_JSON = 1001;
    private static final int REQUEST_SAVE_JSON = 1002;

    private WebView webView;
    private ValueCallback<Uri[]> filePathCallback;
    private String pendingExportData;
    private File installedHtml;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().setStatusBarColor(Color.rgb(16, 23, 27));
        getWindow().setNavigationBarColor(Color.rgb(16, 23, 27));

        installedHtml = new File(getFilesDir(), "haushaltsbuch/index.html");

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(16, 23, 27));
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setBlockNetworkLoads(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);

        webView.addJavascriptInterface(new AndroidBridge(), "AndroidBridge");
        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(
                    WebView webView,
                    ValueCallback<Uri[]> filePathCallbackNew,
                    FileChooserParams fileChooserParams) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = filePathCallbackNew;

                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("application/json");
                intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"application/json", "text/json", "text/plain"});
                startActivityForResult(intent, REQUEST_OPEN_JSON);
                return true;
            }
        });

        if (installedHtml.exists() && installedHtml.length() > 0) {
            loadInstalledApp();
        } else {
            showFirstRunDialog();
        }
    }

    private void showFirstRunDialog() {
        new AlertDialog.Builder(this)
                .setTitle("Haushaltsbuch einrichten")
                .setMessage("Wähle einmal die Haushaltsbuch-HTML-Datei aus deinem Download-Ordner aus. Danach startet die App immer direkt und funktioniert vollständig offline.")
                .setCancelable(false)
                .setPositiveButton("HTML-Datei auswählen", (dialog, which) -> chooseAppHtml())
                .show();
    }

    private void chooseAppHtml() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("text/html");
        intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"text/html", "application/xhtml+xml", "text/plain"});
        startActivityForResult(intent, REQUEST_OPEN_APP_HTML);
    }

    private void installHtml(Uri uri) throws Exception {
        File parent = installedHtml.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IllegalStateException("App-Ordner konnte nicht erstellt werden.");
        }
        try (InputStream input = getContentResolver().openInputStream(uri);
             OutputStream output = new FileOutputStream(installedHtml, false)) {
            if (input == null) throw new IllegalStateException("Datei konnte nicht gelesen werden.");
            byte[] buffer = new byte[16384];
            int read;
            while ((read = input.read(buffer)) != -1) output.write(buffer, 0, read);
            output.flush();
        }
        if (installedHtml.length() < 1024) throw new IllegalStateException("Die gewählte Datei ist zu klein oder ungültig.");
    }

    private void loadInstalledApp() {
        webView.loadUrl("file://" + installedHtml.getAbsolutePath());
    }

    public class AndroidBridge {
        @JavascriptInterface
        public void exportData(String json, String filename) {
            pendingExportData = json;
            runOnUiThread(() -> {
                Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("application/json");
                intent.putExtra(Intent.EXTRA_TITLE, safeFilename(filename));
                startActivityForResult(intent, REQUEST_SAVE_JSON);
            });
        }

        @JavascriptInterface
        public void printPage() {
            runOnUiThread(() -> {
                PrintManager printManager = (PrintManager) getSystemService(Context.PRINT_SERVICE);
                if (printManager == null) {
                    Toast.makeText(MainActivity.this, "Druckdienst nicht verfügbar.", Toast.LENGTH_SHORT).show();
                    return;
                }
                PrintDocumentAdapter adapter = webView.createPrintDocumentAdapter("Haushaltsbuch");
                printManager.print("Haushaltsbuch", adapter, new PrintAttributes.Builder().build());
            });
        }

        @JavascriptInterface
        public void selectAppFile() {
            runOnUiThread(() -> chooseAppHtml());
        }
    }

    private String safeFilename(String name) {
        String fallback = "Haushaltsbuch_Datensicherung.json";
        if (name == null || name.trim().isEmpty()) return fallback;
        return name.replaceAll("[\\\\/:*?\"<>|]", "_");
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);

        if (requestCode == REQUEST_OPEN_APP_HTML) {
            if (resultCode == RESULT_OK && data != null && data.getData() != null) {
                try {
                    installHtml(data.getData());
                    Toast.makeText(this, "Haushaltsbuch installiert.", Toast.LENGTH_SHORT).show();
                    loadInstalledApp();
                } catch (Exception error) {
                    Toast.makeText(this, "HTML-Datei konnte nicht installiert werden: " + error.getMessage(), Toast.LENGTH_LONG).show();
                    if (!installedHtml.exists()) showFirstRunDialog();
                }
            } else if (!installedHtml.exists()) {
                showFirstRunDialog();
            }
            return;
        }

        if (requestCode == REQUEST_OPEN_JSON) {
            if (filePathCallback == null) return;
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null && data.getData() != null) result = new Uri[]{data.getData()};
            filePathCallback.onReceiveValue(result);
            filePathCallback = null;
            return;
        }

        if (requestCode == REQUEST_SAVE_JSON) {
            if (resultCode == RESULT_OK && data != null && data.getData() != null && pendingExportData != null) {
                Uri uri = data.getData();
                try (OutputStream output = getContentResolver().openOutputStream(uri, "w")) {
                    if (output == null) throw new IllegalStateException("Kein Ausgabestream");
                    output.write(pendingExportData.getBytes(StandardCharsets.UTF_8));
                    output.flush();
                    Toast.makeText(this, "Datensicherung gespeichert.", Toast.LENGTH_SHORT).show();
                } catch (Exception error) {
                    Toast.makeText(this, "Speichern fehlgeschlagen: " + error.getMessage(), Toast.LENGTH_LONG).show();
                }
            }
            pendingExportData = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.removeJavascriptInterface("AndroidBridge");
            webView.destroy();
        }
        super.onDestroy();
    }
}
