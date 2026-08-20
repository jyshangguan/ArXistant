package com.arxistant.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * Foreground service that runs the ArXistant Python server inside the app.
 *
 * The server runs in-process (ARXISTANT_IN_PROCESS=1) on 127.0.0.1:8765 with
 * its data directory in app-private storage, and uses an Android Keystore-backed
 * SecretStore for the Nutstore app password.
 */
public class ServerService extends Service {
    private static final String CHANNEL_ID = "arxistant_server";
    private static final int NOTIFICATION_ID = 1;

    private Thread serverThread;

    @Override
    public void onCreate() {
        super.onCreate();
        startForeground(NOTIFICATION_ID, buildNotification());
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (serverThread == null) {
            serverThread = new Thread(this::startPythonServer, "arxistant-python");
            serverThread.start();
        }
        return START_STICKY;
    }

    private void startPythonServer() {
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }
        // Writable data directory (DB, model, cloud config) inside app storage.
        String dataDir = getFilesDir().getAbsolutePath() + "/arxistant";
        // Keystore-backed secret store passed to Python as a callable backend.
        SecretStore secretStore = new SecretStore(this);

        Python.getInstance()
                .getModule("android_bootstrap")
                .callAttr("start_server", dataDir, secretStore);
    }

    private Notification buildNotification() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "ArXistant server", NotificationManager.IMPORTANCE_LOW);
        nm.createNotificationChannel(channel);

        // Use a real app icon in production; ic_menu_view is only a placeholder.
        return new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("ArXistant")
                .setContentText("Paper ranking server is running")
                .setSmallIcon(android.R.drawable.ic_menu_view)
                .setOngoing(true)
                .build();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
