plugins {
    id("com.android.application") version "8.9.2" apply false
    id("org.jetbrains.kotlin.android") version "2.1.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.1.20" apply false
    id("com.chaquo.python") version "17.0.0" apply false
}

tasks.named<org.gradle.api.tasks.wrapper.Wrapper>("wrapper") {
    gradleVersion = "8.14.3"
    distributionType = org.gradle.api.tasks.wrapper.Wrapper.DistributionType.BIN
    distributionSha256Sum = "bd71102213493060956ec229d946beee57158dbd89d0e62b91bca0fa2c5f3531"
}
