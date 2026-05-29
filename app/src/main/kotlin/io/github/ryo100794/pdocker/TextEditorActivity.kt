package io.github.ryo100794.pdocker

import android.content.pm.ActivityInfo
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import java.io.File

class TextEditorActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_NOSENSOR
        val requested = intent.getStringExtra(EXTRA_PATH).orEmpty()
        val file = resolveProjectFile(requested)
        setContentView(CodeEditorView(this, file, MAX_EDIT_BYTES) { name ->
            defaultContent(file, name)
        })
    }

    private fun resolveProjectFile(requested: String): File {
        val projects = File(filesDir, "pdocker/projects").apply { mkdirs() }.canonicalFile
        val skydnirHome = File(filesDir, "pdocker").canonicalFile
        val requestedRoot = intent.getStringExtra(EXTRA_ROOT_PATH)
            ?.takeIf { it.isNotBlank() }
            ?.let { File(it).canonicalFile }
        val allowedRoot = requestedRoot?.takeIf {
            it.toPath().startsWith(skydnirHome.toPath())
        } ?: projects
        val candidate = if (requested.isBlank()) {
            File(projects, "default/Dockerfile")
        } else {
            File(requested)
        }
        val canonical = candidate.canonicalFile
        require(canonical.toPath().startsWith(allowedRoot.toPath())) {
            getString(R.string.editor_path_outside_fmt, allowedRoot.absolutePath)
        }
        return canonical
    }

    private fun defaultContent(file: File, name: String): String =
        when (name) {
            "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml" ->
                "services:\n  app:\n    image: ubuntu:22.04\n    command: [\"/bin/bash\", \"-lc\", \"echo hello from compose\"]\n"
            "Dockerfile" ->
                if (file.parentFile?.name == "default") {
                    assets.open("default-project/Dockerfile").bufferedReader().use { it.readText() }
                } else {
                    "FROM ubuntu:22.04\nCMD [\"/bin/bash\", \"-lc\", \"echo hello from Dockerfile\"]\n"
                }
            else -> ""
        }

    companion object {
        const val EXTRA_PATH = "io.github.ryo100794.pdocker.extra.EDITOR_PATH"
        const val EXTRA_ROOT_PATH = "io.github.ryo100794.pdocker.extra.EDITOR_ROOT_PATH"
        private const val MAX_EDIT_BYTES = 512 * 1024
    }
}
