/* libsodium-compatible crypto_box_seal (sealed box) on top of tweetnacl + blake2b.
   nonce = blake2b(ephemeral_pk || recipient_pk, 24); output = ephemeral_pk || box(msg, nonce, recipient_pk, ephemeral_sk) */
(function () {
  function seal(message, recipientPk) {
    var nacl = window.nacl, b2 = window.blakejs;
    var eph = nacl.box.keyPair();
    var nin = new Uint8Array(64); nin.set(eph.publicKey, 0); nin.set(recipientPk, 32);
    var nonce = b2.blake2b(nin, null, 24);
    var boxed = nacl.box(message, nonce, recipientPk, eph.secretKey);
    var out = new Uint8Array(32 + boxed.length); out.set(eph.publicKey, 0); out.set(boxed, 32);
    eph.secretKey.fill(0);
    return out;
  }
  function b64ToBytes(b64) { var s = atob(b64), a = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; }
  function bytesToB64(a) { var s = ""; for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]); return btoa(s); }
  /** Encrypt a GitHub Actions secret value with the repo public key (base64). Returns base64. */
  window.sealSecret = function (value, publicKeyB64) {
    return bytesToB64(seal(new TextEncoder().encode(value), b64ToBytes(publicKeyB64)));
  };
})();
