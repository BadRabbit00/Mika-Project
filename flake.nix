{
  description = "BlogAI development and checks with Python 3.12 and uv2nix";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs [
        "x86_64-linux"
        "aarch64-linux"
      ];

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      editableOverlay = workspace.mkEditablePyprojectOverlay {
        root = "$REPO_ROOT";
      };

      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;
        in
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.wheel
              overlay
            ]
          )
      );

    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          virtualenv = pythonSet.mkVirtualEnv "blogai-dev-env" workspace.deps.all;
        in
        {
          bootstrap = pkgs.mkShell {
            packages = [
              pkgs.python312
              pkgs.uv
              pkgs.git
            ];
            UV_PYTHON = pkgs.python312.interpreter;
            UV_PYTHON_DOWNLOADS = "never";
          };
          default = pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.uv
              pkgs.git
              pkgs.nixfmt
              pkgs.graphify
            ];
            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = pythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
              PYTHONTZPATH = "${pkgs.tzdata}/share/zoneinfo";
            };
            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel)
            '';
          };
        }
      );

      packages = forAllSystems (system: {
        default = pythonSets.${system}.mkVirtualEnv "blogai-env" workspace.deps.default;
        embedding-model = nixpkgs.legacyPackages.${system}.fetchurl {
          url = "https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/0f741b5a6585bd53aeb15cd1372c56f2a0f65e12/embeddinggemma-300M-Q8_0.gguf";
          sha256 = "b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63";
        };
      });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/blogai";
          meta.description = "Initialize and migrate BlogAI SQLite storage";
        };
      });

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          testEnv = pythonSets.${system}.mkVirtualEnv "blogai-test-env" workspace.deps.all;
        in
        {
          tests =
            pkgs.runCommand "blogai-checks"
              {
                nativeBuildInputs = [
                  testEnv
                  pkgs.uv
                  pkgs.nixfmt
                ];
                PYTHONTZPATH = "${pkgs.tzdata}/share/zoneinfo";
                UV_PYTHON = pkgs.python312.interpreter;
                UV_PYTHON_DOWNLOADS = "never";
                UV_NO_CACHE = "1";
              }
              ''
                cp -r ${lib.cleanSource ./.} source
                chmod -R u+w source
                cd source
                uv lock --check --offline
                pytest -q
                ruff check src tests scripts
                ruff format --check src tests scripts
                nixfmt --check flake.nix
                touch "$out"
              '';
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
