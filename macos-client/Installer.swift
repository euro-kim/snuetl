import AppKit
import Foundation

let manager = FileManager.default
let alert = NSAlert()
alert.messageText = "Install SNUETL for Codex"
alert.informativeText = "This installs the local Canvas helper and a user-level Codex skill. You will connect your SNU eTL account from Codex after installation."
alert.addButton(withTitle: "Install")
alert.addButton(withTitle: "Cancel")

if alert.runModal() == .alertFirstButtonReturn {
    do {
        guard let resources = Bundle.main.resourceURL else {
            throw NSError(domain: "SNUETL", code: 1, userInfo: [NSLocalizedDescriptionKey: "Installer resources are missing"])
        }
        let home = manager.homeDirectoryForCurrentUser
        let appRoot = home.appendingPathComponent("Library/Application Support/SNUETL Codex", isDirectory: true)
        let helperSource = resources.appendingPathComponent("payload/bin", isDirectory: true)
        let helperTarget = appRoot.appendingPathComponent("bin", isDirectory: true)
        let helperStaging = appRoot.appendingPathComponent("bin.next", isDirectory: true)
        let skillSource = resources.appendingPathComponent("payload/skill", isDirectory: true)
        let skillTarget = home.appendingPathComponent(".agents/skills/snuetl", isDirectory: true)
        let skillStaging = home.appendingPathComponent(".agents/skills/snuetl.next", isDirectory: true)

        try manager.createDirectory(at: appRoot, withIntermediateDirectories: true, attributes: nil)
        try manager.createDirectory(at: skillTarget.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: nil)
        for staging in [helperStaging, skillStaging] where manager.fileExists(atPath: staging.path) {
            try manager.removeItem(at: staging)
        }
        try manager.copyItem(at: helperSource, to: helperStaging)
        try manager.copyItem(at: skillSource, to: skillStaging)
        for target in [helperTarget, skillTarget] where manager.fileExists(atPath: target.path) {
            try manager.removeItem(at: target)
        }
        try manager.moveItem(at: helperStaging, to: helperTarget)
        try manager.moveItem(at: skillStaging, to: skillTarget)

        let success = NSAlert()
        success.messageText = "SNUETL is ready in Codex"
        success.informativeText = "Open a new Codex chat and say: $snuetl connect"
        success.runModal()
    } catch {
        let failure = NSAlert(error: error)
        failure.runModal()
    }
}
