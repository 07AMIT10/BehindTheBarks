package com.btb.ondevice.net

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.security.keystore.KeyInfo
import android.security.keystore.StrongBoxUnavailableException
import java.util.Base64
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature

/**
 * Signs the pairing challenge with a key that never leaves the device (T2.5).
 *
 * The key lives in the AndroidKeyStore (EC P-256, sign-only) so a copied APK or a stolen database row
 * cannot impersonate the station. `Signer` is an interface so JVM unit tests can inject a software key
 * (the Keystore itself is unavailable there).
 */
interface Signer {
    fun sign(data: ByteArray): ByteArray
    fun publicSpkiB64(): String
}

object StationKey {
    private const val ALIAS = "btb_station_key"
    private const val PROVIDER = "AndroidKeyStore"

    /** The device's key: strong-box backed when the hardware offers it, otherwise the TEE. */
    fun getOrCreate(): Signer = getOrCreate(ALIAS)

    /**
     * Same as [getOrCreate] under any alias. Only the on-device instrumented test passes a throwaway
     * alias (so it never touches the production key); the app always uses [ALIAS].
     */
    fun getOrCreate(alias: String): Signer = KeystoreSigner(getOrCreatePrivateKey(alias), alias)

    /** Removes a key created by [getOrCreate] under [alias]. Used by the instrumented test cleanup. */
    fun delete(alias: String) {
        KeyStore.getInstance(PROVIDER).apply { load(null) }.deleteEntry(alias)
    }

    private fun getOrCreatePrivateKey(alias: String): PrivateKey {
        val ks = KeyStore.getInstance(PROVIDER).apply { load(null) }
        (ks.getKey(alias, null) as? PrivateKey)?.let { return it }
        val generator = KeyPairGenerator.getInstance("EC", PROVIDER)
        val spec = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(java.security.spec.ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(false) // the station signs unattended, screen dimmed
            .build()
        try {
            generator.initialize(spec)
        } catch (_: StrongBoxUnavailableException) {
            generator.initialize(spec) // the spec already prefers strong box; the fallback is implicit
        }
        generator.generateKeyPair()
        return (KeyStore.getInstance(PROVIDER).apply { load(null) }.getKey(alias, null) as PrivateKey)
    }

    private class KeystoreSigner(private val key: PrivateKey, private val alias: String) : Signer {
        override fun sign(data: ByteArray): ByteArray =
            Signature.getInstance("SHA256withECDSA").run { initSign(key); update(data); sign() }

        override fun publicSpkiB64(): String =
            key.factoryEncoded().let { Base64.getEncoder().encodeToString(it) }

        private fun PrivateKey.factoryEncoded(): ByteArray {
            val ks = KeyStore.getInstance(PROVIDER).apply { load(null) }
            val cert = ks.getCertificate(alias)
            return cert.publicKey.encoded // DER SubjectPublicKeyInfo, what POST /pair expects
        }
    }

    /** Debug helper: which hardware backs the key. Keystore itself is verified on-device. */
    fun describe(): String = try {
        val ks = KeyStore.getInstance(PROVIDER).apply { load(null) }
        val key = ks.getKey(ALIAS, null) as? PrivateKey ?: return "absent"
        val info = KeyFactory.getInstance(key.algorithm, PROVIDER)
            .getKeySpec(key, KeyInfo::class.java) as KeyInfo
        "EC ${info.keySize}bit"  // isStrongBoxBacked is API 31+: keep the debug line simple
    } catch (t: Throwable) {
        "unknown (${t.javaClass.simpleName})"
    }
}
