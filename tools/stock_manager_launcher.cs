using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher
{
    [STAThread]
    private static void Main()
    {
        try
        {
            string root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
            string python = Path.Combine(root, @".venv\Scripts\pythonw.exe");
            string script = Path.Combine(root, "stock_batch_manager.py");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("Keep this launcher in output\\stock_manager and set up the repository's .venv.");
            Process.Start(new ProcessStartInfo(python, "\"" + script + "\"") {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true
            });
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Adobe Stock Batch Manager", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
