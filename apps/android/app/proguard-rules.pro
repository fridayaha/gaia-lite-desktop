# Hilt
-keep class dagger.hilt.** { *; }
-keep class * extends dagger.hilt.android.internal.lifecycle.HiltViewModelFactory$ViewModelFactoriesEntryPoint { *; }

# Retrofit
-keepattributes Signature, InnerClasses, EnclosingMethod
-keepattributes RuntimeVisibleAnnotations, RuntimeVisibleParameterAnnotations
-keepclassmembers,allowshrinking,allowobfuscation interface * {
    @retrofit2.http.* <methods>;
}
-dontwarn javax.annotation.**
-dontwarn kotlin.Unit
-dontwarn retrofit2.**

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# kotlinx.serialization
-keepattributes *Annotation*
-keepattributes Signature, InnerClasses, EnclosingMethod
-keepclassmembers class **$$serializer { *; }
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.unionagents.enduser.**$$serializer { *; }
-keepclassmembers class com.unionagents.enduser.** {
    *** Companion;
}
-keepclasseswithmembers class com.unionagents.enduser.** {
    kotlinx.serialization.KSerializer serializer(...);
}
# Keep all @Serializable DTOs and their fields to avoid R8 stripping defaults / serializers.
-keep @kotlinx.serialization.Serializable class com.unionagents.enduser.net.dto.** { *; }
-keepclassmembers @kotlinx.serialization.Serializable class com.unionagents.enduser.net.dto.** { *; }

# Compose
-keep class androidx.compose.** { *; }
-dontwarn androidx.compose.**
