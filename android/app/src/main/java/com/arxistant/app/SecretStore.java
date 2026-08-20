package com.arxistant.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.HashMap;
import java.util.Map;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Android Keystore-backed secret store.
 *
 * The ArXistant Python code (arxistant_secrets.set_backend) calls get/set/delete
 * on this object via Chaquopy's Java-to-Python bridge. Values are AES-GCM
 * encrypted with a key held in the Android Keystore and the ciphertext is stored
 * in app-private SharedPreferences (never plaintext).
 */
public class SecretStore {
    private static final String KEY_ALIAS = "arxistant_master_key";
    private static final String PREFS = "arxistant_secrets";
    private static final int GCM_IV_LENGTH = 12;

    private final SharedPreferences prefs;
    private final Map<String, String> cache = new HashMap<>();

    public SecretStore(Context context) {
        this.prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        ensureKey();
    }

    public String get(String key) {
        String cached = cache.get(key);
        if (cached != null) {
            return cached;
        }
        String encoded = prefs.getString(key, null);
        if (encoded == null) {
            return null;
        }
        try {
            String value = decrypt(encoded);
            cache.put(key, value);
            return value;
        } catch (Exception e) {
            return null;
        }
    }

    public void set(String key, String value) {
        try {
            prefs.edit().putString(key, encrypt(value)).apply();
            cache.put(key, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to store secret", e);
        }
    }

    public void delete(String key) {
        prefs.edit().remove(key).apply();
        cache.remove(key);
    }

    private void ensureKey() {
        try {
            KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
            ks.load(null);
            if (!ks.containsAlias(KEY_ALIAS)) {
                KeyGenerator kg = KeyGenerator.getInstance(
                        KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
                kg.init(new KeyGenParameterSpec.Builder(
                        KEY_ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .build());
                kg.generateKey();
            }
        } catch (Exception e) {
            throw new RuntimeException("Failed to init Android Keystore", e);
        }
    }

    private SecretKey getKey() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        return ((KeyStore.SecretKeyEntry) ks.getEntry(KEY_ALIAS, null)).getSecretKey();
    }

    private String encrypt(String plaintext) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, getKey());
        byte[] iv = cipher.getIV();
        byte[] ciphertext = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));
        byte[] combined = new byte[iv.length + ciphertext.length];
        System.arraycopy(iv, 0, combined, 0, iv.length);
        System.arraycopy(ciphertext, 0, combined, iv.length, ciphertext.length);
        return Base64.encodeToString(combined, Base64.NO_WRAP);
    }

    private String decrypt(String encoded) throws Exception {
        byte[] combined = Base64.decode(encoded, Base64.NO_WRAP);
        byte[] iv = new byte[GCM_IV_LENGTH];
        byte[] ciphertext = new byte[combined.length - GCM_IV_LENGTH];
        System.arraycopy(combined, 0, iv, 0, GCM_IV_LENGTH);
        System.arraycopy(combined, GCM_IV_LENGTH, ciphertext, 0, ciphertext.length);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, getKey(), new GCMParameterSpec(128, iv));
        return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
    }
}
