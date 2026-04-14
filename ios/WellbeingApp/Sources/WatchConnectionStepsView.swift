import SwiftUI

struct WatchConnectionStepsView: View {
    @ObservedObject var watchBridge: PhoneWatchBridge
    
    let steps = ["DOWNLOADED", "REACHABLE", "CONNECTED"]
    
    var themeColor: Color {
        watchBridge.currentConnectionStep >= 2 ? .green : .orange
    }
    
    var body: some View {
        VStack(spacing: 16) {
            Text("Watch Connection Progress")
                .font(.headline)
                .frame(maxWidth: .infinity, alignment: .leading)
                
            HStack(spacing: 0) {
                ForEach(0..<steps.count, id: \.self) { index in
                    VStack(spacing: 10) {
                        ZStack {
                            // Background Line
                            HStack(spacing: 0) {
                                Rectangle()
                                    .fill(index == 0 ? Color.clear : (index <= watchBridge.currentConnectionStep ? themeColor : Color.gray.opacity(0.3)))
                                    .frame(height: 2)
                                Rectangle()
                                    .fill(index == steps.count - 1 ? Color.clear : (index < watchBridge.currentConnectionStep ? themeColor : Color.gray.opacity(0.3)))
                                    .frame(height: 2)
                            }
                            
                            // Node
                            ZStack {
                                if index < watchBridge.currentConnectionStep {
                                    // Completed Step (Checkmark)
                                    Circle()
                                        .fill(themeColor)
                                        .frame(width: 24, height: 24)
                                    Image(systemName: "checkmark")
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundColor(.white)
                                } else if index == watchBridge.currentConnectionStep {
                                    // Current Step (Ring with inner dot)
                                    Circle()
                                        .fill(themeColor)
                                        .frame(width: 24, height: 24)
                                    Circle()
                                        .fill(Color.white)
                                        .frame(width: 8, height: 8)
                                } else {
                                    // Upcoming Step (Small point)
                                    Circle()
                                        .fill(Color.gray.opacity(0.4))
                                        .frame(width: 10, height: 10)
                                }
                            }
                        }
                        
                        // Stepper Label
                        Text(steps[index])
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(index <= watchBridge.currentConnectionStep ? themeColor : Color.gray.opacity(0.6))
                    }
                }
            }
            .padding(.top, 4)
            
            // Detailed status text centered below
            Text(statusMessage)
                .font(.caption)
                .foregroundColor(themeColor)
                .multilineTextAlignment(.center)
                .padding(.bottom, 4)
        }
    }
    
    var statusMessage: String {
        switch watchBridge.currentConnectionStep {
        case 0:
            return "App not installed on Apple Watch"
        case 1:
            return "App installed. Wake your watch or open the app to establish connection."
        case 2, 3:
            return "Watch is connected and ready to capture data."
        default:
            return "Unknown state"
        }
    }
}
