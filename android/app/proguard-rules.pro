# Add project specific ProGuard rules here.
# By default, the flags in this file are appended to flags specified
# in $ANDROID_HOME/tools/proguard/proguard-android.txt

# Keep the LiteRT JNI entry points.
-keep class com.google.ai.edge.litert.** { *; }

# kotlinx.serialization keeps its generated serializers on the companion/class itself.
-keepattributes *Annotation*, InnerClasses, Signature, RuntimeVisible*Annotations