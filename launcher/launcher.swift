// Starts the real command and waits for it. launchd runs this instead of `uv` so that macOS
// attributes Calendar and Reminders access to this signed program, whose Info.plist explains
// why Jarvis needs it. Everything it starts inherits the permission.
import Foundation

let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else {
    FileHandle.standardError.write(Data("usage: jarvis-launch <command> [args...]\n".utf8))
    exit(64)
}
let process = Process()
process.executableURL = URL(fileURLWithPath: command)
process.arguments = Array(args.dropFirst())
do { try process.run() } catch {
    FileHandle.standardError.write(Data("jarvis-launch: \(error)\n".utf8))
    exit(127)
}
signal(SIGTERM, SIG_IGN)
let term = DispatchSource.makeSignalSource(signal: SIGTERM)
term.setEventHandler { process.terminate() }
term.resume()
process.waitUntilExit()
exit(process.terminationStatus)
