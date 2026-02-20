<?php

declare(strict_types=1);

namespace App;

use Symfony\Bundle\FrameworkBundle\Kernel\MicroKernelTrait;
use Symfony\Component\HttpKernel\Kernel as BaseKernel;

/**
 * The Symfony application kernel - the heart of the Symfony framework.
 *
 * This is the entry point that boots the framework, loads bundles (see config/bundles.php),
 * reads configuration (see config/packages/*.yaml), and wires up the dependency injection
 * container (see config/services.yaml).
 *
 * MicroKernelTrait provides a simplified setup where configuration files are auto-discovered
 * from the config/ directory - no manual registration needed.
 *
 * You rarely need to edit this file. Most configuration happens in config/ YAML files.
 */
class Kernel extends BaseKernel
{
    use MicroKernelTrait;
}
