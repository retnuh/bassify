import typer

app = typer.Typer(help="Isolate bass from stereo practice tracks.", no_args_is_help=True)


@app.callback()
def _main() -> None:
    pass


if __name__ == "__main__":
    app()
