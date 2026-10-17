package com.btb.ondevice.net

import android.os.Build
import android.security.keystore.KeyInfo
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.KeyStore
import java.security.PrivateKey
import java.security.PublicKey
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import java.util.UUID

/**
 * Runs on the phone against the real AndroidKeyStore. Uses a throwaway alias that is deleted after
 * each test, so the production station key ("btb_station_key") is never touched.
 */
@RunWith(AndroidJUnit4::class)
class StationKeyInstrumentedTest {
    private val alias = "btb_test_key_" + UUID.randomUUID().toString().replace("-", "")

    @After
    fun deleteThrowawayKey() {
        assertNotEquals("the production alias must never be used here", "btb_station_key", alias)
        StationKey.delete(alias)
    }

    private fun publicKeyOf(signer: Signer): ECPublicKey {
        val spki = Base64.getDecoder().decode(signer.publicSpkiB64())
        return KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as ECPublicKey
    }

    private fun verify(pub: PublicKey, data: ByteArray, sig: ByteArray): Boolean =
        Signature.getInstance("SHA256withECDSA").run {
            initVerify(pub)
            update(data)
            verify(sig)
        }

    @Test
    fun exportedSpkiParsesAsSecp256r1() {
        val pub = publicKeyOf(StationKey.getOrCreate(alias))
        val expected = AlgorithmParameters.getInstance("EC").run {
            init(ECGenParameterSpec("secp256r1"))
            getParameterSpec(ECParameterSpec::class.java)
        }
        assertEquals(expected.curve, pub.params.curve)
        assertEquals(expected.order, pub.params.order)
        assertEquals(256, pub.params.curve.field.fieldSize)
        Log.i("BTB-AT", "station key SPKI parses as EC, curve matches secp256r1, fieldBits=${pub.params.curve.field.fieldSize}")
    }

    @Test
    fun signatureVerifiesOnlyOverTheSignedBytes() {
        val signer = StationKey.getOrCreate(alias)
        val pub = publicKeyOf(signer)
        val data = "pairing-challenge-${UUID.randomUUID()}".toByteArray()
        val sig = signer.sign(data)

        assertTrue("signature over the same bytes must verify", verify(pub, data, sig))
        assertFalse(
            "signature over different bytes must not verify",
            verify(pub, "some other challenge".toByteArray(), sig),
        )
    }

    @Test
    fun privateKeyIsNotExtractableAndLogsItsSecurityLevel() {
        StationKey.getOrCreate(alias)
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val priv = ks.getKey(alias, null) as PrivateKey

        // Keystore private keys have no exportable encoding at all.
        assertNull("a keystore private key must not export its encoded form", priv.encoded)

        val info = KeyFactory.getInstance(priv.algorithm, "AndroidKeyStore")
            .getKeySpec(priv, KeyInfo::class.java) as KeyInfo
        assertEquals(256, info.keySize)
        @Suppress("DEPRECATION")
        val insideSecureHardware = info.isInsideSecureHardware
        // securityLevel is API 31+; the phone under test is API 36. Logged only: a software-backed
        // key on some devices is not a failure of the app.
        val level = if (Build.VERSION.SDK_INT >= 31) info.securityLevel.toString() else "n/a(api<31)"
        Log.i(
            "BTB-AT",
            "station key: algorithm=${priv.algorithm} keySize=${info.keySize} " +
                "insideSecureHardware=$insideSecureHardware securityLevel=$level sdk=${Build.VERSION.SDK_INT}",
        )
    }
}
