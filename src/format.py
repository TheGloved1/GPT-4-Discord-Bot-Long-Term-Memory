input_file = "main.py"
output_file = "main-formated.py"


def format_python_file(input_file, output_file):
    with open(input_file, "r") as infile, open(output_file, "w") as outfile:
        prev_indent = None
        prev_line = ""
        for line in infile:
            stripped_line = line.strip()
            current_indent = len(line) - len(line.lstrip())
            if stripped_line == "" and prev_line.strip() == "":
                continue
            if stripped_line == "" and prev_indent is not None:
                outfile.write(" " * prev_indent + "\n")
            else:
                outfile.write(line)
                prev_indent = current_indent
            prev_line = line


if __name__ == "__main__":
    format_python_file(input_file, output_file)
