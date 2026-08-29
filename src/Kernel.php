<?php

declare(strict_types=1);

namespace App;

use Symfony\Bundle\FrameworkBundle\Kernel\MicroKernelTrait;
use Symfony\Component\HttpKernel\Kernel as BaseKernel;

/**
 * Boots Symfony once, before the clinician's browser ever reaches a `/scribe` route.
 *
 * Nothing here is visible on screen, yet every visible thing depends on it. Booting assembles three groups:
 *
 * - bundles from `config/bundles.php`, which is where Twig page rendering and Mercure live streaming come from
 * - parameters from `config/packages/*.yaml`, including the WebSocket and Mercure URLs the page hands to the browser
 * - services from `config/services.yaml`, which registers everything under `src/` for autowiring and points every logger at the JSON logger
 *
 * `MicroKernelTrait` discovers all of it under `config/` on its own, so this class stays empty and real changes belong in the YAML.
 */
class Kernel extends BaseKernel
{
    use MicroKernelTrait;
}
