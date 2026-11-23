using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class DevelopmentLauncher
{
    [STAThread]
    private static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
        string script = Path.Combine(root, "development.py");
        try
        {
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("Falta el entorno .venv o el archivo development.py.");

            using (Process child = Process.Start(new ProcessStartInfo
            {
                FileName = python,
                Arguments = "\"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            }))
            {
                child.WaitForExit();
                if (child.ExitCode != 0 && child.ExitCode != 2)
                    throw new InvalidOperationException("Python terminó con el código " + child.ExitCode + ".");
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message + "\n\nRevisa el entorno del proyecto en " + root,
                            "JMangaShelf · Desarrollo", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
